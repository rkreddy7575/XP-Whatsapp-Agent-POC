import json
import logging
import re
from typing import Any, Dict, List, Optional

from services.catalogue_service import catalogue_service, is_category_browsing_intent, is_image_request_intent
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
        self._pending_media_messages: Dict[str, List[Dict[str, Any]]] = {}

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

        # Fast-Path C: Category Browsing ("Categories", "category", "show categories", "browse categories", etc.)
        if is_category_browsing_intent(clean_text):
            # Clear old product candidates so subsequent number selection selects category
            conversation_service.set_candidates(conv_id, [])
            reply = catalogue_service.format_category_menu()
            self._finalize_reply(conv_id, "SHOW_CATEGORIES", reply)
            return reply

        # Fast-Path D: Category Selection (by number when viewing categories OR by direct category name)
        sel_idx = self._extract_selection_index(clean_text)
        # Subcase 1: By index when category menu was just displayed or candidates are empty
        if sel_idx and conv.last_intent == "SHOW_CATEGORIES":
            cat_from_idx = catalogue_service.get_category_by_index(sel_idx)
            if cat_from_idx:
                candidates = catalogue_service.search_products(category=cat_from_idx, limit=5)
                reply = self._present_product_candidates(
                    conv_id, customer_phone, candidates, category_title=cat_from_idx
                )
                self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
                return reply

        # Subcase 2: By direct category name / alias (e.g. "Gift Sets", "combos", "mugs", "water bottles")
        matched_cat = catalogue_service.match_category_name(clean_text)
        if matched_cat:
            candidates = catalogue_service.search_products(category=matched_cat, limit=5)
            reply = self._present_product_candidates(
                conv_id, customer_phone, candidates, category_title=matched_cat
            )
            self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
            return reply

        # Fast-Path E0: Image / Photo Request ("can you show me image", "show me images", "photos", etc.)
        if is_image_request_intent(clean_text):
            return self._handle_image_request(conv, customer_phone)

        # Fast-Path E: Active Product Candidate Selection (by index e.g. "1", "2", "second one" or by candidate SKU e.g. "GS-002")
        if conv.current_product_candidates:
            # 1. By index
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

        # Fast-Path E1: Standalone Quantity for Selected Product (e.g. ".50", "50", "100", "50 units", "100 pcs")
        if conv.selected_sku:
            standalone_qty = self._extract_standalone_quantity(clean_text)
            if standalone_qty is not None:
                reply = self._handle_direct_quote(conv_id, customer_phone, conv.selected_sku, standalone_qty)
                self._finalize_reply(conv_id, IntentType.PRICE_QUOTE.value, reply)
                return reply

        # Fast-Path F: Direct Exact SKU Discovery (e.g. "GS-002", "XG-501", "GS-001")
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

    def _present_product_candidates(
        self,
        conv_id: str,
        customer_phone: str,
        candidates: List[Dict[str, Any]],
        category_title: Optional[str] = None,
        quantity: Optional[int] = None,
    ) -> str:
        """
        Stores candidates, queues rich media cards for WhatsApp, and formats presentation.
        Avoids duplicate product text when image cards are dispatched.
        """
        if not candidates:
            title_disp = category_title or "that category"
            return (
                f"🔍 *No products currently found in \"{title_disp}\".*\n\n"
                "Reply with *Categories* to explore all available collections."
            )

        conversation_service.set_candidates(conv_id, candidates)
        target_qty = quantity or 100
        if quantity:
            conversation_service.set_selected_quantity(conv_id, quantity)

        clean_phone = customer_phone.lstrip("+").strip()
        media_list = self._build_candidate_media_messages(
            candidates, target_qty=target_qty
        )
        self._pending_media_messages[clean_phone] = media_list

        # When all candidates have rich image cards dispatched, send ONE clean selection prompt
        if len(media_list) == len(candidates) and candidates:
            if len(candidates) == 1:
                return "👉 Reply with 1 to select this product."
            elif len(candidates) == 2:
                return "👉 Reply with 1 or 2 to select a product."
            else:
                nums_str = ", ".join(str(i) for i in range(1, len(candidates))) + f", or {len(candidates)}"
                return f"👉 Reply with {nums_str} to select a product."

        # If some candidates have images and some do not, only print text for the ones without images
        if media_list:
            img_skus = {m["sku"] for m in media_list}
            text_lines = []
            for idx, c in enumerate(candidates, 1):
                if c.get("sku") not in img_skus:
                    sku = c.get("sku", "")
                    name = c.get("name") or c.get("category", "Product")
                    colors = ", ".join(c.get("colors") or [])
                    opt_str = f"\n   🎨 Options: {colors}" if colors else ""
                    text_lines.append(f"{idx}️⃣ *{name}*\n   🏷️ SKU: `{sku}`{opt_str}")

            nums_str = ", ".join(str(i) for i in range(1, len(candidates))) + f", or {len(candidates)}"
            prompt = f"👉 Reply with {nums_str} to select a product."
            if text_lines:
                extra = "\n\n".join(text_lines)
                return f"ℹ️ *Additional options:*\n\n{extra}\n\n{prompt}"
            return prompt

        return catalogue_service.format_product_presentation(candidates, quantity=quantity)

    def _build_candidate_media_messages(
        self,
        candidates: List[Dict[str, Any]],
        target_qty: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Builds rich WhatsApp image media items with clean captions for candidates with real image URLs.
        Preserves candidate order strictly (1 -> candidates[0], 2 -> candidates[1], etc.).
        """
        emoji_badges = {
            1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
            6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
        }
        media_list = []
        for idx, item in enumerate(candidates, 1):
            sku = item.get("sku", "")
            img_url = item.get("image_url") or catalogue_service.get_image_url(sku)
            if img_url:
                cat = item.get("category", "")
                subcat = item.get("subcategory", "")
                if item.get("name"):
                    prod_name = item["name"]
                elif subcat and subcat != cat:
                    prod_name = f"{cat} — {subcat}"
                else:
                    prod_name = cat or "Product"

                badge = emoji_badges.get(idx, f"{idx}.")
                lines = [
                    f"{badge} {prod_name}",
                    f"SKU: {sku}",
                ]
                colors = item.get("colors")
                if colors:
                    if isinstance(colors, list):
                        lines.append(f"Options: {', '.join(str(c) for c in colors)}")
                    elif isinstance(colors, str):
                        lines.append(f"Options: {colors}")

                caption = "\n".join(lines)
                media_list.append({
                    "image_url": img_url,
                    "caption": caption,
                    "sku": sku,
                    "index": idx,
                })
        return media_list

    def _handle_image_request(
        self,
        conv,
        customer_phone: str,
    ) -> str:
        """
        Handles requests to view images/photos of current product candidates.
        Dispatches media messages with real Supabase image URLs and preserves candidate state.
        If no candidates exist, returns helpful guidance rather than performing a keyword search.
        """
        conv_id = conv.conversation_id
        candidates = conv.current_product_candidates

        if not candidates:
            reply = (
                "Sure — tell me the product or category you'd like to see images for (e.g. *Mugs*, *Gift Sets*, or *XG-501*).\n\n"
                "• Reply with *Categories* to browse all collections."
            )
            self._finalize_reply(conv_id, IntentType.GENERAL_HELP.value, reply)
            return reply

        clean_phone = customer_phone.lstrip("+").strip()
        target_qty = conv.selected_quantity or 100
        media_list = self._build_candidate_media_messages(candidates, target_qty=target_qty)
        self._pending_media_messages[clean_phone] = media_list

        with_img_skus = {m["sku"] for m in media_list}
        missing_images = [
            (idx, c) for idx, c in enumerate(candidates, 1)
            if c.get("sku") not in with_img_skus
        ]

        if len(media_list) == len(candidates):
            # All candidates have images
            if len(candidates) == 1:
                reply = "👉 Reply with 1 to select this product."
            elif len(candidates) == 2:
                reply = "👉 Reply with 1 or 2 to select a product."
            else:
                nums_str = ", ".join(str(i) for i in range(1, len(candidates))) + f", or {len(candidates)}"
                reply = f"👉 Reply with {nums_str} to select a product."
        elif media_list:
            # Some candidates have images, some do not
            missing_lines = []
            for idx, c in missing_images:
                sku = c.get("sku", "")
                name = c.get("name") or c.get("category", "Product")
                missing_lines.append(f"• *{idx}. {sku}* ({name})")
            missing_text = "\n".join(missing_lines)
            reply = (
                f"📸 *Sent available product photos above.*\n\n"
                f"ℹ️ *Photos are currently not on file for:*\n"
                f"{missing_text}\n\n"
                "Reply with the item number (e.g. *1*, *2*) or product code to select and get an instant quote."
            )
        else:
            # None of the candidates have images on file
            lines = []
            for idx, c in enumerate(candidates, 1):
                sku = c.get("sku", "")
                name = c.get("name") or c.get("category", "Product")
                lines.append(f"*{idx}. {sku}* — {name}")
            list_text = "\n".join(lines)
            reply = (
                f"ℹ️ *Photos are not currently on file for these items, but complete specifications and pricing are available:*\n\n"
                f"{list_text}\n\n"
                "Reply with the item number (e.g. *1*, *2*) or product code to select and get an instant quote."
            )

        # Update last_intent to SHOW_IMAGES, but preserve conv.current_product_candidates!
        self._finalize_reply(conv_id, "SHOW_IMAGES", reply)
        return reply

    @staticmethod
    def _extract_selection_index(text: str) -> Optional[int]:
        """Extracts 1-based selection index from numeric or ordinal text (supports 1..20)."""
        if not text:
            return None
        clean = re.sub(r'[*_~`"\'\u201c\u201d\u2018\u2019]', '', text).strip().lower()

        m_num = re.match(r"^#?([1-9]|1[0-9]|20)\.?$", clean)
        if m_num:
            return int(m_num.group(1))

        ordinals = {
            "first": 1, "1st": 1, "one": 1,
            "second": 2, "2nd": 2, "two": 2,
            "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4,
            "fifth": 5, "5th": 5, "five": 5,
            "sixth": 6, "6th": 6, "six": 6,
            "seventh": 7, "7th": 7, "seven": 7,
            "eighth": 8, "8th": 8, "eight": 8,
            "ninth": 9, "9th": 9, "nine": 9,
            "tenth": 10, "10th": 10, "ten": 10,
        }
        m = re.search(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th)\b", clean)
        if m and m.group(1) in ordinals:
            return ordinals[m.group(1)]

        m_opt = re.search(r"^(?:option|item|choice|number|#|category)?\s*([1-9]|1[0-9]|20)\.?$", clean)
        if m_opt:
            return int(m_opt.group(1))

        return None

    def get_pending_media_messages(self, customer_phone: str) -> List[Dict[str, Any]]:
        clean_phone = customer_phone.lstrip("+").strip()
        return self._pending_media_messages.pop(clean_phone, [])

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
            # Record quote in SupabaseQuoteRepository if configured
            try:
                from services.supabase_repository import SupabaseClient, SupabaseQuoteRepository, SupabaseEnquiryRepository
                sb = SupabaseClient()
                if sb.is_configured:
                    SupabaseQuoteRepository(sb).create_quote({
                        "quote_number": f"QUO-{customer_phone}-{int(datetime.now().timestamp())}",
                        "customer_phone": customer_phone,
                        "customer_name": None,
                        "sku": canonical_sku,
                        "quantity": quantity,
                        "unit_price_excl_gst": quote.unit_price_excl_gst,
                        "gst_percentage": quote.gst_percentage,
                        "unit_gst": quote.unit_gst,
                        "unit_price_incl_gst": quote.unit_price_incl_gst,
                        "total_price_excl_gst": quote.total_price_excl_gst,
                        "total_gst": quote.total_gst,
                        "total_price_incl_gst": quote.total_price_incl_gst,
                        "status": "ISSUED",
                    })
                    SupabaseEnquiryRepository(sb).create_enquiry({
                        "customer_phone": customer_phone,
                        "sku": canonical_sku,
                        "quantity": quantity,
                        "status": "QUOTED",
                    })
            except Exception:
                pass

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
            # Mark enquiry converted in Supabase if configured
            try:
                from services.supabase_repository import SupabaseClient, SupabaseEnquiryRepository
                sb = SupabaseClient()
                if sb.is_configured:
                    SupabaseEnquiryRepository(sb).create_enquiry({
                        "customer_phone": customer_phone,
                        "sku": pending.sku,
                        "quantity": pending.quantity,
                        "status": "CONVERTED",
                    })
            except Exception:
                pass
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
                return self._present_product_candidates(
                    conv_id, customer_phone, candidates, quantity=intent.quantity
                )
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
    def _extract_standalone_quantity(text: str) -> Optional[int]:
        """
        Extracts a standalone integer quantity from customer text.
        Handles punctuation typos (e.g. '.50', '50.', ',50', '#50'),
        plain numbers ('50', '100', '250'),
        and unit suffixes ('50 units', '100 pcs', '50 pieces', 'qty 50', 'need 100').
        """
        if not text:
            return None
        clean = text.strip()
        # Standalone numbers with optional punctuation or unit suffixes
        m = re.search(
            r"^\s*[.,#\s]*(?:qty|quantity|need|for|just|around|about)?\s*[:\-]?\s*(\d+)\s*(?:units?|pcs?|pieces?|nos?|items?)?[.,\s]*$",
            clean,
            re.IGNORECASE,
        )
        if m:
            val = int(m.group(1))
            if 1 <= val <= 100000:
                return val
        # Explicit "qty: 50" or "quantity is 50"
        m2 = re.search(r"\b(?:qty|quantity)\s*(?:is|=|:)?\s*(\d+)\b", clean, re.IGNORECASE)
        if m2:
            val = int(m2.group(1))
            if 1 <= val <= 100000:
                return val
        return None

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
