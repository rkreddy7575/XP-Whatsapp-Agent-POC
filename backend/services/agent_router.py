import json
import logging
import re
from typing import Any, Dict, List, Optional

from services.catalogue_service import catalogue_service
from services.conversation_models import MessageDirection
from services.conversation_service import conversation_service
from services.gemini_models import IntentType, StructuredIntent
from services.gemini_service import gemini_service
from services.inventory_service import inventory_service
from services.order_models import OrderStatus
from services.order_service import order_service
from services.pricing_service import PriceQuoteResult, pricing_service

logger = logging.getLogger("agent_router")


class AgentRouter:
    """
    Orchestration layer connecting customer WhatsApp messages, Gemini NLU intent
    classification, conversation state persistence, and authoritative business services.
    """

    def __init__(self):
        pass

    def handle_incoming_message(
        self,
        customer_phone: str,
        message_text: str,
        customer_name: Optional[str] = None,
    ) -> str:
        """
        Main entrypoint for processing incoming customer messages.
        Persists message history, updates multi-turn state, and produces authoritative responses.
        """
        clean_text = message_text.strip() if message_text else ""

        # Retrieve or initialize conversation state
        conv = conversation_service.get_or_create_conversation(customer_phone)
        conv_id = conv.conversation_id

        # Persist inbound customer message
        conversation_service.add_message(conv_id, MessageDirection.INBOUND, message_text or "")

        if not clean_text:
            reply = catalogue_service.resolve_customer_intent(message_text or "")
            self._finalize_reply(conv_id, IntentType.GENERAL_HELP.value, reply)
            return reply

        # ---------------------------------------------------------------------
        # 1. Deterministic Fast-Paths (0 Quota Consumption, 100% Reliable)
        # ---------------------------------------------------------------------

        # Fast-Path A: Deterministic SKU + Quantity (e.g. "XG-GS-501 100")
        sku_qty = pricing_service.extract_sku_and_quantity(
            clean_text, sku_resolver=catalogue_service.get_by_sku
        )
        if sku_qty:
            sku, quantity = sku_qty
            reply = self._handle_direct_quote(conv_id, customer_phone, sku, quantity)
            self._finalize_reply(conv_id, IntentType.PRICE_QUOTE.value, reply)
            return reply

        # Fast-Path B: Deterministic Order Confirmation (e.g. "CONFIRM", "YES")
        if order_service.is_confirmation_intent(clean_text):
            reply = self._handle_order_confirmation(conv_id, customer_phone, customer_name)
            self._finalize_reply(conv_id, IntentType.CONFIRM_ORDER.value, reply)
            return reply

        # Fast-Path C: Active Product Candidate Selection (by index e.g. "1", "2", "second one" or by candidate SKU e.g. "GS-002")
        if conv.current_product_candidates:
            # 1. By index
            sel_idx = self._extract_selection_index(clean_text)
            candidate = None
            if sel_idx and 1 <= sel_idx <= len(conv.current_product_candidates):
                candidate = conv.current_product_candidates[sel_idx - 1]
            else:
                # 2. By matching SKU in active candidates list
                norm_input = re.sub(r'[^A-Z0-9]', '', clean_text.upper())
                for cand in conv.current_product_candidates:
                    cand_sku_norm = re.sub(r'[^A-Z0-9]', '', cand.get("sku", "").upper())
                    if cand_sku_norm and cand_sku_norm == norm_input:
                        candidate = cand
                        break

            if candidate:
                sku = candidate.get("sku")
                qty = conv.selected_quantity
                if qty and qty > 0:
                    reply = self._handle_direct_quote(conv_id, customer_phone, sku, qty)
                    self._finalize_reply(conv_id, IntentType.SELECT_PRODUCT.value, reply)
                    return reply
                else:
                    conversation_service.set_selected_product(conv_id, sku)
                    cat = candidate.get("category", "Product")
                    reply = (
                        f"👍 You selected *`{sku}`* ({cat}).\n\n"
                        f"📦 *Please tell me the quantity you need* (e.g. *100* or *250 units*) for an instant quotation."
                    )
                    self._finalize_reply(conv_id, IntentType.SELECT_PRODUCT.value, reply)
                    return reply

        # Fast-Path D: Direct Exact SKU Discovery (e.g. "GS-002", "XG-501", "GS-001")
        exact_product = catalogue_service.get_by_sku(clean_text)
        if exact_product:
            canonical_sku = exact_product.get("sku", clean_text)
            conversation_service.set_selected_product(conv_id, canonical_sku)
            reply = catalogue_service.format_product_card(exact_product)
            self._finalize_reply(conv_id, IntentType.PRODUCT_DETAILS.value, reply)
            return reply

        pricing_rule = pricing_service.get_any_rule_for_sku(clean_text)
        if pricing_rule:
            display_sku = clean_text.strip().upper()
            cat_display = pricing_rule.source_category or "Corporate Gift"
            reply = (
                f"🎁 *Product Code:* `{display_sku}`\n"
                f"📂 *Category:* {cat_display}\n"
                f"🎨 *Colors / Options:* Standard / Single Finish\n"
                f"💰 *Price:* Based on quantity\n"
                f"📦 *Please tell me the quantity you need.*\n\n"
                f"_Note: Reply with the product code and quantity (e.g., *{display_sku} 100*) for an instant quotation._"
            )
            self._finalize_reply(conv_id, IntentType.PRODUCT_DETAILS.value, reply)
            return reply

        # Fast-Path E: Single-word "categories"
        if clean_text.lower() in ("categories", "category", "menu"):
            reply = catalogue_service.format_category_menu()
            self._finalize_reply(conv_id, IntentType.GENERAL_HELP.value, reply)
            return reply

        # Fast-Path F: Pending-quote contextual guidance ("what next", "how to proceed", etc.)
        if conv.pending_quote and self._is_next_step_question(clean_text):
            reply = self._pending_quote_guidance(conv)
            self._finalize_reply(conv_id, IntentType.GENERAL_HELP.value, reply)
            return reply

        # ---------------------------------------------------------------------
        # 2. Conversational Intent Processing via Gemini
        # ---------------------------------------------------------------------
        # Build lightweight context
        history = [
            {"direction": m.direction.value, "message_text": m.message_text}
            for m in conversation_service.get_recent_messages(conv_id, limit=6)
        ]
        context = {
            "selected_sku": conv.selected_sku,
            "selected_quantity": conv.selected_quantity,
            "has_pending_quote": bool(conv.pending_quote),
            "candidates_count": len(conv.current_product_candidates),
        }

        intent = gemini_service.parse_intent(clean_text, history=history, context=context)

        # ---------------------------------------------------------------------
        # 3. Intent Routing Layer to Business Services
        # ---------------------------------------------------------------------
        reply = self._route_intent(conv, customer_phone, customer_name, clean_text, intent)
        self._finalize_reply(conv_id, intent.intent.value, reply)
        return reply

    @staticmethod
    def _extract_selection_index(text: str) -> Optional[int]:
        """Extracts 1-based selection index from numeric or ordinal text."""
        clean = text.strip().lower()
        if re.match(r"^[1-9]$", clean):
            return int(clean)

        ordinals = {
            "first": 1, "1st": 1, "one": 1,
            "second": 2, "2nd": 2, "two": 2,
            "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4,
            "fifth": 5, "5th": 5, "five": 5,
        }
        m = re.search(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b", clean)
        if m and m.group(1) in ordinals:
            return ordinals[m.group(1)]

        m_opt = re.search(r"^(?:option|item|choice|number|#)?\s*([1-9])$", clean)
        if m_opt:
            return int(m_opt.group(1))

        return None

    def _finalize_reply(self, conv_id: str, intent_name: str, reply: str) -> None:
        """Persists outbound response and updates conversation metadata."""
        conversation_service.add_message(conv_id, MessageDirection.OUTBOUND, reply)
        conversation_service.update_last_intent(conv_id, intent_name)

    def _handle_direct_quote(
        self, conv_id: str, customer_phone: str, sku: str, quantity: int
    ) -> str:
        """Calculates price quote and caches pending quotation in conversation and order services."""
        # Find canonical product name/category if available
        product = catalogue_service.get_by_sku(sku)
        canonical_sku = product.get("sku", sku) if product else sku
        product_name = None
        if product:
            cat = product.get("category", "")
            subcat = product.get("subcategory", "")
            product_name = f"{cat} — {subcat}" if subcat and subcat != cat else cat

        quote = pricing_service.calculate_total(canonical_sku, quantity)
        if quote.available:
            order_service.set_pending_quote(customer_phone, quote)
            conversation_service.set_pending_quote(conv_id, quote)
            conversation_service.set_selected_product(conv_id, canonical_sku, quantity)

        stock_status = inventory_service.get_stock_status_for_quote(canonical_sku, quantity)
        return pricing_service.format_quotation(quote, stock_status=stock_status, product_name=product_name)

    def _handle_order_confirmation(
        self, conv_id: str, customer_phone: str, customer_name: Optional[str]
    ) -> str:
        """Confirms an active pending quotation into a permanent order."""
        # Check order_service session or conversation pending quote
        pending = order_service.get_pending_quote(customer_phone)
        if not pending:
            # Check conversation_service
            raw_quote = conversation_service.get_pending_quote(conv_id)
            if raw_quote:
                try:
                    pending = PriceQuoteResult(**raw_quote)
                    order_service.set_pending_quote(customer_phone, pending)
                except Exception:
                    pending = None

        if pending:
            order = order_service.confirm_pending_order(customer_phone, customer_name=customer_name)
            conversation_service.clear_pending_quote(conv_id)
            conversation_service.clear_selection(conv_id)
            return order_service.format_order_confirmation(order)
        else:
            return (
                "ℹ️ You don't have an active quotation pending confirmation.\n\n"
                "Please send a product code and quantity (e.g., *GS-002 100*) to get an instant quote first!"
            )

    def _route_intent(
        self,
        conv: Any,
        customer_phone: str,
        customer_name: Optional[str],
        message_text: str,
        intent: StructuredIntent,
    ) -> str:
        """Dispatches structured intent to the appropriate deterministic service."""
        conv_id = conv.conversation_id

        # ---------------------------------------------------------------------
        # INTENT: PRODUCT_SEARCH
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.PRODUCT_SEARCH:
            vague_words = {"something", "anything", "stuff", "items", "item", "product", "products"}
            raw_q = (intent.query or "").strip().lower()
            if raw_q in vague_words and not intent.category and not intent.sku:
                return (
                    "👋 I'd be happy to help you find the right corporate gift!\n\n"
                    "Could you let me know what category you're interested in (e.g. *gift sets*, *bottles*, *pens*, *notebooks*) "
                    "or your target quantity and budget?"
                )

            # Query CatalogueService
            max_budget = intent.budget_per_unit
            candidates = catalogue_service.search_products(
                query=intent.query,
                category=intent.category,
                max_price=max_budget,
                limit=5,
            )

            # Fallback relaxation if no strict category+budget matches
            if not candidates and intent.category:
                candidates = catalogue_service.search_products(
                    category=intent.category, limit=5
                )

            if not candidates and intent.query:
                candidates = catalogue_service.search_products(
                    query=intent.query, limit=5
                )

            if candidates:
                conversation_service.set_candidates(conv_id, candidates)
                if intent.quantity:
                    conversation_service.set_selected_quantity(conv_id, intent.quantity)

                return catalogue_service.format_product_presentation(candidates)
            else:
                search_term = intent.query or intent.category or "that item"
                return (
                    f"🔍 I couldn't find any products matching \"{search_term}\".\n\n"
                    "Reply with *categories* to explore all options, or ask for a specific category like *gift sets*, *bottles*, or *pens*."
                )

        # ---------------------------------------------------------------------
        # INTENT: SELECT_PRODUCT
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.SELECT_PRODUCT:
            target_idx = intent.selection_index
            candidate = None
            if target_idx:
                candidate = conversation_service.get_candidate_by_index(conv_id, target_idx)

            if not candidate and intent.sku:
                candidate = catalogue_service.get_by_sku(intent.sku)

            if candidate:
                sku = candidate.get("sku")
                qty = intent.quantity or conv.selected_quantity

                conversation_service.set_selected_product(conv_id, sku, quantity=qty)

                if qty and qty > 0:
                    return self._handle_direct_quote(conv_id, customer_phone, sku, qty)
                else:
                    cat = candidate.get("category", "Product")
                    return (
                        f"👍 You selected *`{sku}`* ({cat}).\n\n"
                        f"📦 *Please tell me the quantity you need* (e.g. *100* or *250 units*) for an instant quotation."
                    )
            else:
                if not conv.current_product_candidates:
                    return (
                        "ℹ️ There are no active products to select from.\n\n"
                        "Please search for products first (e.g. *gift sets under 500*, *water bottles*) "
                        "or send a product code like *GS-002*."
                    )
                return (
                    f"Please choose a valid item number from the list above (1 to {len(conv.current_product_candidates)}), "
                    "or send a product code like *GS-002*."
                )

        # ---------------------------------------------------------------------
        # INTENT: PRICE_QUOTE
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.PRICE_QUOTE:
            target_sku = intent.sku or conv.selected_sku
            target_qty = intent.quantity or conv.selected_quantity

            # Check if customer just sent a number like "100"
            if not target_qty and re.match(r"^\d+$", message_text.strip()):
                try:
                    target_qty = int(message_text.strip())
                except ValueError:
                    pass

            if target_sku and target_qty:
                return self._handle_direct_quote(conv_id, customer_phone, target_sku, target_qty)
            elif target_sku and not target_qty:
                conversation_service.set_selected_product(conv_id, target_sku)
                return f"📦 How many units of *`{target_sku}`* do you need? (e.g. *100*, *250*)"
            elif not target_sku and target_qty:
                conversation_service.set_selected_quantity(conv_id, target_qty)
                return f"Got it, {target_qty} units! Which product code or category are you interested in? (e.g. *XG-GS-501* or *gift sets*)"
            else:
                # If category mentioned, perform search
                if intent.category or intent.query:
                    intent.intent = IntentType.PRODUCT_SEARCH
                    return self._route_intent(conv, customer_phone, customer_name, message_text, intent)
                return "Please tell me the product code and quantity you need (e.g. *XG-GS-501 100*)."

        # ---------------------------------------------------------------------
        # INTENT: PRODUCT_DETAILS
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.PRODUCT_DETAILS:
            sku = intent.sku or conv.selected_sku
            if sku:
                product = catalogue_service.get_by_sku(sku)
                if product:
                    conversation_service.set_selected_product(conv_id, sku)
                    return catalogue_service.format_product_card(product)
            return "Please provide the product code (e.g. *XG-501*) you'd like details for."

        # ---------------------------------------------------------------------
        # INTENT: CONFIRM_ORDER
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.CONFIRM_ORDER:
            return self._handle_order_confirmation(conv_id, customer_phone, customer_name)

        # ---------------------------------------------------------------------
        # INTENT: CANCEL_ORDER
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.CANCEL_ORDER:
            order_service.clear_pending_quote(customer_phone)
            conversation_service.clear_selection(conv_id)
            return "🚫 Your active quotation and selections have been cleared. Let me know if you would like to explore anything else!"

        # ---------------------------------------------------------------------
        # INTENT: ORDER_STATUS
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.ORDER_STATUS:
            if intent.order_id:
                order = order_service.get_order(intent.order_id)
            else:
                orders = order_service.list_customer_orders(customer_phone)
                order = orders[0] if orders else None

            if order:
                st_display = order.status.value.replace("_", " ")
                date_display = order.created_at[:10] if order.created_at else "Recent"
                return (
                    f"📋 *Order Status: `{order.order_id}`*\n"
                    f"🏷️ *Status:* {st_display}\n"
                    f"💰 *Grand Total:* ₹{order.grand_total:,.2f} *(incl. GST)*\n"
                    f"📅 *Date:* {date_display}\n"
                    f"📦 *Stock Status:* Availability confirmation required\n\n"
                    f"_Our team will update you when your order moves to dispatch!_"
                )
            else:
                return "ℹ️ No recent orders found for your number. Send a product code and quantity (e.g., *XG-GS-501 100*) to place an order!"

        # ---------------------------------------------------------------------
        # INTENT: GENERAL_HELP or UNKNOWN
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.GENERAL_HELP:
            # Safety net: If there's a pending quote, give contextual guidance
            # instead of the generic welcome message
            if conv.pending_quote:
                return self._pending_quote_guidance(conv)

            return (
                "👋 *Welcome to Mudhra Branding Solutions!* 🎁\n\n"
                "We provide custom-branded corporate gifts, promotional products, and executive sets.\n\n"
                "You can ask me to:\n"
                "• *Find products by budget* (e.g. _\"I need 100 gift sets under 500 each\"_)\n"
                "• *Get instant quotes* (e.g. _\"Quote for XG-GS-501 100\"_)\n"
                "• *Browse categories* (reply *categories*)\n"
                "• *Check order status* (reply *order status*)\n\n"
                "How can I help you today?"
            )

        # Fallback for UNKNOWN: Delegate to deterministic catalogue discovery
        cat_reply = catalogue_service.resolve_customer_intent(message_text)
        if cat_reply:
            return cat_reply

        return (
            "Sorry, I couldn't process that request right now. "
            "Please send a product code and quantity, for example *XG-GS-501 100*, "
            "or reply *categories* to browse our catalogue."
        )

    @staticmethod
    def _is_next_step_question(text: str) -> bool:
        """
        Deterministically detects 'what next' / 'how to proceed' style questions.
        Used to avoid Gemini overhead and guarantee correct pending-quote context.
        """
        clean = text.strip().lower()
        # Remove leading punctuation/question marks for matching
        clean = re.sub(r'[^\w\s]', '', clean).strip()

        next_step_phrases = {
            "what next",
            "what now",
            "next",
            "what should i do",
            "what should i do next",
            "what do i do",
            "what do i do next",
            "how do i proceed",
            "how to proceed",
            "how can i proceed",
            "how do i order",
            "how can i order",
            "how do i place the order",
            "how do i place order",
            "how to order",
            "how to place the order",
            "how to place order",
            "now what",
            "whats next",
            "what is next",
            "proceed",
            "go ahead",
            "next step",
            "next steps",
            "and then",
            "then what",
        }
        return clean in next_step_phrases

    @staticmethod
    def _pending_quote_guidance(conv: Any) -> str:
        """
        Returns contextual next-step guidance when a pending quote exists.
        Includes the quoted SKU and quantity for clarity.
        """
        sku = conv.selected_sku or "your selected product"
        qty = conv.selected_quantity
        qty_str = f" ({qty} units)" if qty else ""

        return (
            f"📋 Your quotation for *`{sku}`*{qty_str} is ready.\n\n"
            "✅ Reply *CONFIRM* to place the order.\n"
            "🔄 Reply *change quantity* to update the quantity.\n"
            "🔀 Reply *change product* to select a different product.\n"
            "❌ Reply *cancel* to clear this quotation.\n\n"
            "_What would you like to do?_"
        )


agent_router = AgentRouter()
