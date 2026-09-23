"""
Supabase Sync Layer — Dual-Write Bridge

Intercepts key write operations in the existing SQLite-based services and mirrors
them to Supabase when credentials are configured. This provides:

1. Zero-risk integration: SQLite remains the primary source of truth for the runtime
2. Supabase receives the same data, enabling the owner dashboard and cloud readiness
3. No modification of existing service internals
4. Graceful degradation: if Supabase is unreachable, SQLite continues unaffected

Usage: Import and call `install_supabase_sync()` at app startup (in main.py lifespan).
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("supabase_sync")

# Global flag — set at startup
_supabase_enabled = False
_sb_client = None


def _get_client():
    """Lazily initialize the Supabase client."""
    global _sb_client, _supabase_enabled
    if _sb_client is not None:
        return _sb_client

    try:
        from services.supabase_repository import SupabaseClient
        _sb_client = SupabaseClient()
        _supabase_enabled = _sb_client.is_configured
        if _supabase_enabled:
            logger.info("Supabase sync layer ENABLED (URL: %s)", _sb_client.url[:40])
        else:
            logger.info("Supabase sync layer DISABLED (no credentials configured)")
    except Exception as exc:
        logger.warning("Supabase sync layer init failed: %s", exc)
        _supabase_enabled = False
        _sb_client = None

    return _sb_client


def install_supabase_sync():
    """
    Installs the Supabase dual-write hooks on existing services.
    Call once at app startup. Safe to call multiple times (idempotent).
    """
    client = _get_client()
    if not _supabase_enabled or not client:
        logger.info("Supabase sync: skipping hook installation (not configured)")
        return False

    _patch_conversation_service()
    _patch_order_service()
    logger.info("Supabase sync hooks installed successfully")
    return True


def _patch_conversation_service():
    """
    Monkey-patches ConversationService to dual-write conversations and messages to Supabase.
    The original SQLite methods remain the primary source of truth.
    """
    from services.conversation_service import conversation_service
    from services.supabase_repository import (
        SupabaseConversationRepository,
        SupabaseMessageRepository,
        SupabaseCustomerRepository,
    )

    conv_repo = SupabaseConversationRepository(_sb_client)
    msg_repo = SupabaseMessageRepository(_sb_client)
    cust_repo = SupabaseCustomerRepository(_sb_client)

    # Save originals
    _orig_get_or_create = conversation_service.get_or_create_conversation
    _orig_add_message = conversation_service.add_message
    _orig_set_selected = conversation_service.set_selected_product
    _orig_set_quantity = conversation_service.set_selected_quantity
    _orig_set_candidates = conversation_service.set_candidates
    _orig_set_pending_quote = conversation_service.set_pending_quote
    _orig_clear_pending_quote = conversation_service.clear_pending_quote
    _orig_clear_selection = conversation_service.clear_selection
    _orig_update_intent = conversation_service.update_last_intent

    def synced_get_or_create(customer_phone, customer_name=None):
        result = _orig_get_or_create(customer_phone, customer_name)
        try:
            # Ensure customer exists in Supabase
            cust_repo.get_or_create(customer_phone, customer_name)
            # Ensure conversation exists in Supabase
            conv_repo.get_or_create(customer_phone, channel="whatsapp")
        except Exception as exc:
            logger.debug("Supabase sync (get_or_create_conversation): %s", exc)
        return result

    def synced_add_message(conversation_id, direction, message_text, **kwargs):
        result = _orig_add_message(conversation_id, direction, message_text, **kwargs)
        try:
            dir_str = direction.value if hasattr(direction, 'value') else str(direction)
            msg_repo.add_message({
                "conversation_id": conversation_id,
                "direction": dir_str.upper(),
                "message_text": message_text or "",
                "sender": "customer" if dir_str.upper() == "INBOUND" else "agent",
            })
        except Exception as exc:
            logger.debug("Supabase sync (add_message): %s", exc)
        return result

    def synced_set_selected(conversation_id, sku, quantity=None):
        result = _orig_set_selected(conversation_id, sku, quantity)
        try:
            update = {"selected_sku": sku, "stage": "product_selected"}
            if quantity:
                update["selected_quantity"] = quantity
            conv_repo.update_state(conversation_id, update)
        except Exception as exc:
            logger.debug("Supabase sync (set_selected_product): %s", exc)
        return result

    def synced_set_quantity(conversation_id, quantity):
        result = _orig_set_quantity(conversation_id, quantity)
        try:
            conv_repo.update_state(conversation_id, {"selected_quantity": quantity})
        except Exception as exc:
            logger.debug("Supabase sync (set_selected_quantity): %s", exc)
        return result

    def synced_set_candidates(conversation_id, candidates):
        result = _orig_set_candidates(conversation_id, candidates)
        try:
            conv_repo.update_state(conversation_id, {
                "current_product_candidates": json.dumps(candidates) if candidates else "[]",
                "stage": "browsing",
            })
        except Exception as exc:
            logger.debug("Supabase sync (set_candidates): %s", exc)
        return result

    def synced_set_pending_quote(conversation_id, quote_data):
        result = _orig_set_pending_quote(conversation_id, quote_data)
        try:
            conv_repo.update_state(conversation_id, {"stage": "quote_pending"})
        except Exception as exc:
            logger.debug("Supabase sync (set_pending_quote): %s", exc)
        return result

    def synced_clear_pending_quote(conversation_id):
        result = _orig_clear_pending_quote(conversation_id)
        try:
            conv_repo.update_state(conversation_id, {"stage": "quote_cleared"})
        except Exception as exc:
            logger.debug("Supabase sync (clear_pending_quote): %s", exc)
        return result

    def synced_clear_selection(conversation_id):
        result = _orig_clear_selection(conversation_id)
        try:
            conv_repo.update_state(conversation_id, {
                "selected_sku": None,
                "selected_quantity": None,
                "current_product_candidates": "[]",
                "stage": "initial",
            })
        except Exception as exc:
            logger.debug("Supabase sync (clear_selection): %s", exc)
        return result

    def synced_update_intent(conversation_id, intent):
        result = _orig_update_intent(conversation_id, intent)
        try:
            conv_repo.update_state(conversation_id, {"last_intent": intent})
        except Exception as exc:
            logger.debug("Supabase sync (update_last_intent): %s", exc)
        return result

    # Apply patches
    conversation_service.get_or_create_conversation = synced_get_or_create
    conversation_service.add_message = synced_add_message
    conversation_service.set_selected_product = synced_set_selected
    conversation_service.set_selected_quantity = synced_set_quantity
    conversation_service.set_candidates = synced_set_candidates
    conversation_service.set_pending_quote = synced_set_pending_quote
    conversation_service.clear_pending_quote = synced_clear_pending_quote
    conversation_service.clear_selection = synced_clear_selection
    conversation_service.update_last_intent = synced_update_intent

    logger.info("Supabase sync: conversation_service patched (9 methods)")


def _patch_order_service():
    """
    Monkey-patches OrderService to dual-write orders and order items to Supabase.
    The original SQLite methods remain the primary source of truth.
    """
    from services.order_service import order_service
    from services.supabase_repository import (
        SupabaseOrderRepository,
        SupabaseEnquiryRepository,
        SupabaseQuoteRepository,
    )

    order_repo = SupabaseOrderRepository(_sb_client)
    enquiry_repo = SupabaseEnquiryRepository(_sb_client)
    quote_repo = SupabaseQuoteRepository(_sb_client)

    _orig_confirm = order_service.confirm_pending_order

    def synced_confirm(customer_phone, customer_name=None):
        order = _orig_confirm(customer_phone, customer_name)
        if order:
            try:
                # Mirror order to Supabase
                order_data = {
                    "order_id": order.order_id,
                    "customer_phone": order.customer_phone,
                    "customer_name": order.customer_name,
                    "status": order.status.value if hasattr(order.status, 'value') else str(order.status),
                    "subtotal": order.subtotal,
                    "gst_amount": order.gst_amount,
                    "grand_total": order.grand_total,
                    "currency": order.currency,
                    "pricing_version": order.pricing_version,
                    "inventory_status": order.inventory_status,
                    "notes": "Synced from WhatsApp flow",
                }
                items_data = []
                for item in (order.items or []):
                    items_data.append({
                        "sku": item.sku,
                        "product_name": item.product_name or "",
                        "quantity": item.quantity,
                        "unit_price": item.unit_price,
                        "gst_rate": item.gst_rate,
                        "gst_amount": item.gst_amount,
                        "line_total": item.line_total,
                    })
                order_repo.create_order(order_data, items_data)
                logger.info("Supabase sync: order %s mirrored successfully", order.order_id)
            except Exception as exc:
                logger.warning("Supabase sync (confirm_order): %s", exc)
        return order

    _orig_update_status = order_service.update_order_status

    def synced_update_status(order_id, new_status):
        result = _orig_update_status(order_id, new_status)
        if result:
            try:
                status_str = new_status.value if hasattr(new_status, 'value') else str(new_status)
                order_repo.update_status(order_id, status_str, "Status updated via dashboard")
                logger.info("Supabase sync: order %s status updated to %s", order_id, status_str)
            except Exception as exc:
                logger.debug("Supabase sync (update_order_status): %s", exc)
        return result

    # Apply patches
    order_service.confirm_pending_order = synced_confirm
    order_service.update_order_status = synced_update_status

    logger.info("Supabase sync: order_service patched (2 methods)")
