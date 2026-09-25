import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from services.catalogue_service import catalogue_service, extract_candidate_index_from_image_request, extract_multi_product_requirements, extract_sku_and_quantity_from_inquiry, extract_sku_from_image_request, is_category_browsing_intent, is_image_request_intent
from services.conversation_models import MessageDirection
from services.conversation_service import conversation_service
from services.gemini_models import IntentType, StructuredIntent
from services.gemini_service import gemini_service
from services.inventory_service import inventory_service
from services.order_models import OrderStatus
from services.order_service import order_service
from services.pricing_service import PriceQuoteResult, pricing_service

logger = logging.getLogger("agent_router")


def is_show_more_intent(text: str) -> bool:
    """
    Detects user intent to paginate through product search results
    (e.g., 'show more', 'more', 'next', 'other options', 'show me more').
    """
    if not text:
        return False
    clean = text.strip().lower()
    for ch in ['*', '_', '~', '`', '"', "'", chr(8220), chr(8221), chr(8216), chr(8217)]:
        clean = clean.replace(ch, '')
    clean = text.strip().lower()
    for ch in ['*', '_', '~', '`', '"', "'", chr(8220), chr(8221), chr(8216), chr(8217)]:
        clean = clean.replace(ch, '')

    exact_phrases = {
        "show more",
        "show me more",
        "more",
        "next",
        "next page",
        "next batch",
        "more products",
        "show more products",
        "anything else",
        "other options",
        "more options",
        "another one",
        "show another",
        "show me another",
        "other products",
        "see more",
        "view more",
        "more items",
        "different options",
        "different products",
        "next options",
    }
    if clean in exact_phrases:
        return True

    patterns = [
        r"^\s*show\s+(?:me\s+)?more(?:\s+products?|\s+options?|\s+items?)?\s*$",
        r"^\s*more(?:\s+products?|\s+options?|\s+items?|\s+gift\s+sets?)?\s*$",
        r"^\s*next(?:\s+page|\s+products?|\s+options?|\s+one|\s+batch)?\s*$",
        r"^\s*(?:any|anything)\s+else\s*$",
        r"^\s*(?:other|different)\s+(?:options?|products?|items?)\s*$",
        r"^\s*(?:show\s+)?another\s+(?:one|product|option)\s*$",
        r"^\s*(?:see|view)\s+more\s*$",
    ]
    return any(re.match(p, clean, re.IGNORECASE) for p in patterns)


def is_change_product_intent(text: str) -> bool:
    """
    Detects customer intent to return to search results or pick a different product.
    """
    if not text:
        return False
    clean = text.strip().lower()
    for ch in ['*', '_', '~', '`', '"', "'", chr(8220), chr(8221), chr(8216), chr(8217)]:
        clean = clean.replace(ch, '')
    clean = text.strip().lower()
    for ch in ['*', '_', '~', '`', '"', "'", chr(8220), chr(8221), chr(8216), chr(8217)]:
        clean = clean.replace(ch, '')

    exact_phrases = {
        "change product",
        "choose another product",
        "choose another",
        "different product",
        "show alternatives",
        "alternatives",
        "other product",
        "choose different product",
        "pick another",
        "go back",
        "back",
    }
    return clean in exact_phrases


class AgentRouter:
    """
    Orchestration layer connecting customer WhatsApp messages, Gemini NLU intent
    classification, conversation state persistence, and authoritative business services.
    """

    def __init__(self):
        self._pending_media_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._search_contexts: Dict[str, Dict[str, Any]] = {}

    def _get_search_context(self, clean_phone: str, conv: Any) -> Optional[Dict[str, Any]]:
        """Retrieves active search context from in-memory cache or candidate metadata."""
        if clean_phone in self._search_contexts:
            return self._search_contexts[clean_phone]
        if conv and conv.current_product_candidates:
            first = conv.current_product_candidates[0]
            if isinstance(first, dict):
                query = first.get("_search_query")
                category = first.get("_search_category") or first.get("category")
                max_price = first.get("_search_max_price")
                all_shown = first.get("_all_shown_skus") or [
                    c.get("sku") for c in conv.current_product_candidates if isinstance(c, dict) and c.get("sku")
                ]
                page = first.get("_search_page", 1)
                target_qty = first.get("_target_qty") or conv.selected_quantity
                ctx = {
                    "query": query,
                    "category": category,
                    "max_price": max_price,
                    "all_shown_skus": all_shown,
                    "page": page,
                    "target_qty": target_qty,
                }
                self._search_contexts[clean_phone] = ctx
                return ctx
        return None

    def handle_incoming_message(
        self,
        customer_phone: str,
        message_text: str,
        customer_name: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> str:
        """
        Main entrypoint for processing incoming customer messages.
        Persists message history, updates multi-turn state, and produces authoritative responses.
        """
        clean_text = message_text.strip() if message_text else ""
        clean_phone = customer_phone.lstrip("+").strip()

        # Retrieve or initialize conversation state
        conv = conversation_service.get_or_create_conversation(customer_phone)
        conv_id = conv.conversation_id

        # Persist inbound customer message
        conversation_service.add_message(
            conv_id,
            MessageDirection.INBOUND,
            message_text or "",
            channel_message_id=message_id,
        )

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

        # Fast-Path B: Deterministic Order Confirmation (e.g. "CONFIRM", "YES", "BOOK IT", "GO AHEAD")
        if order_service.is_confirmation_intent(clean_text):
            reply = self._handle_order_confirmation(conv_id, customer_phone, customer_name)
            self._finalize_reply(conv_id, IntentType.CONFIRM_ORDER.value, reply)
            return reply

        # Fast-Path C: Category Browsing ("Categories", "category", "show categories", "browse categories", etc.)
        if is_category_browsing_intent(clean_text):
            # Invalidate stale quote, selection, and candidates so browsing starts clean
            conversation_service.invalidate_quote_context(conv_id, customer_phone)
            conversation_service.set_candidates(conv_id, [])
            self._search_contexts.pop(clean_phone, None)
            self._pending_media_messages.pop(clean_phone, None)
            reply = catalogue_service.format_category_menu()
            self._finalize_reply(conv_id, "SHOW_CATEGORIES", reply)
            return reply

        # Fast-Path D: Category Selection (by number when viewing categories OR by direct category name)
        sel_idx = self._extract_selection_index(clean_text)
        # Subcase 1: By index when category menu was just displayed or candidates are empty
        if sel_idx and conv.last_intent == "SHOW_CATEGORIES":
            cat_from_idx = catalogue_service.get_category_by_index(sel_idx)
            if cat_from_idx:
                conversation_service.invalidate_quote_context(conv_id, customer_phone)
                candidates = catalogue_service.search_products(category=cat_from_idx, limit=5)
                all_skus = [c.get("sku") for c in candidates if c.get("sku")]
                for c in candidates:
                    c["_search_category"] = cat_from_idx
                    c["_all_shown_skus"] = all_skus
                    c["_search_page"] = 1
                self._search_contexts[clean_phone] = {
                    "query": None,
                    "category": cat_from_idx,
                    "max_price": None,
                    "all_shown_skus": all_skus,
                    "page": 1,
                    "target_qty": None,
                }
                reply = self._present_product_candidates(
                    conv_id, customer_phone, candidates, category_title=cat_from_idx
                )
                self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
                return reply

        # Subcase 2: By direct category name / alias (e.g. "Gift Sets", "combos", "mugs", "water bottles")
        matched_cat = catalogue_service.match_category_name(clean_text)
        if matched_cat:
            conversation_service.invalidate_quote_context(conv_id, customer_phone)
            candidates = catalogue_service.search_products(category=matched_cat, limit=5)
            all_skus = [c.get("sku") for c in candidates if c.get("sku")]
            for c in candidates:
                c["_search_category"] = matched_cat
                c["_all_shown_skus"] = all_skus
                c["_search_page"] = 1
            self._search_contexts[clean_phone] = {
                "query": None,
                "category": matched_cat,
                "max_price": None,
                "all_shown_skus": all_skus,
                "page": 1,
                "target_qty": None,
            }
            reply = self._present_product_candidates(
                conv_id, customer_phone, candidates, category_title=matched_cat
            )
            self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
            return reply

        # Fast-Path E_QUOTE_QTY: Active Quote Quantity Change (priority before image/search)
        # When pending quote exists, natural quantity expressions resolve BEFORE all other intents
        if conv.pending_quote:
            _early_qty = self._extract_standalone_quantity(clean_text)
            if _early_qty is not None:
                _active_sku = conv.pending_quote.get("sku") or conv.selected_sku
                if _active_sku:
                    reply = self._handle_direct_quote(conv_id, customer_phone, _active_sku, _early_qty)
                    self._finalize_reply(conv_id, IntentType.PRICE_QUOTE.value, reply)
                    return reply

        # Fast-Path E0: Image / Photo Request ("can you show me image", "show me images", "photos", etc.)
        # Also handles SKU-specific: "image XG-MP-01", "photo of XG-MP-01", "picture of XG-MP-01", etc.
        # Also handles candidate-specific: "I need image for 3", "image for 3", "show image 3", etc.
        _image_sku = extract_sku_from_image_request(clean_text)
        if _image_sku or is_image_request_intent(clean_text):
            return self._handle_image_request(conv, customer_phone, clean_text, _image_sku)

        # Fast-Path MULTI_PRODUCT: Multi-Product Requirement Request (e.g. "I need 5 bootles and 10 pens")
        multi_reqs = extract_multi_product_requirements(clean_text)
        if multi_reqs:
            reply = self._handle_multi_product_request(conv_id, customer_phone, multi_reqs)
            self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
            return reply

        # Fast-Path E_MORE: Pagination Continuation ("show more", "next", "more", "other options", etc.)
        # If pending quote exists and text is "next", prioritize pending quote guidance
        if is_show_more_intent(clean_text):
            if conv.pending_quote and self._is_next_step_question(clean_text):
                reply = self._pending_quote_guidance(conv)
                self._finalize_reply(conv_id, IntentType.GENERAL_HELP.value, reply)
                return reply
            return self._handle_show_more(conv, customer_phone)

        # Fast-Path E_CHANGE: Change Product / View Alternatives ("another one", "change product", "go back")
        if is_change_product_intent(clean_text):
            return self._handle_change_product(conv, customer_phone)

        # Fast-Path SKU_INQUIRY: Natural Language SKU Price / Product Inquiry
        # (e.g. "what is the price for GS-135", "price of GS-135", "how much is GS-135",
        #  "GS-135 price", "give me price for GS-135", "what about GS-135", "what is th e price for XG-GS-137")
        _inq_sku, _inq_qty = extract_sku_and_quantity_from_inquiry(clean_text)
        if _inq_sku:
            if _inq_qty is not None and _inq_qty > 0:
                reply = self._handle_direct_quote(conv_id, customer_phone, _inq_sku, _inq_qty)
                self._finalize_reply(conv_id, IntentType.PRICE_QUOTE.value, reply)
                return reply
            elif re.sub(r'[^A-Z0-9]', '', clean_text.upper()) != re.sub(r'[^A-Z0-9]', '', _inq_sku.upper()):
                # Conversational inquiry containing SKU (not a pure exact SKU token)
                # Switch context to the newly requested SKU
                conversation_service.set_selected_product(conv_id, _inq_sku, None)
                conversation_service.clear_pending_quote(conv_id)
                conversation_service.set_candidates(conv_id, [])
                order_service.clear_pending_quote(customer_phone)

                prod = catalogue_service.get_by_sku(_inq_sku)
                if prod:
                    cat = prod.get("category", "")
                    subcat = prod.get("subcategory", "")
                    name = prod.get("name") or (f"{cat} – {subcat}" if subcat and subcat != cat else cat)
                    colors = prod.get("colors")
                    color_str = f"\n🎨 *Options:* {', '.join(colors)}" if colors else ""
                    reply = (
                        f"📦 *Product:* {name}\n"
                        f"🏷️ *SKU:* `{_inq_sku}`\n"
                        f"📂 *Category:* {cat}{color_str}\n\n"
                        f"🔢 *How many units of {_inq_sku} do you need?* (Please tell me the quantity, e.g. *100* or *250 units*)"
                    )
                else:
                    reply = (
                        f"🏷️ *Product Code:* `{_inq_sku}`\n\n"
                        f"🔢 *How many units of {_inq_sku} do you need?* (Please tell me the quantity, e.g. *100* or *250 units*)"
                    )
                self._finalize_reply(conv_id, IntentType.PRODUCT_DETAILS.value, reply)
                return reply

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
                    subcat = candidate.get("subcategory")
                    name = candidate.get("name") or (f"{cat} – {subcat}" if subcat and subcat != cat else cat)
                    colors = candidate.get("colors")
                    color_str = f"\n🎨 *Options:* {', '.join(colors)}" if colors else ""

                    reply = (
                        f"Great choice! You selected: 🎁\n\n"
                        f"📦 *Product:* {name}\n"
                        f"🏷️ *SKU:* `{sku}`\n"
                        f"📂 *Category:* {cat}{color_str}\n\n"
                        f"Would you like a quote?\n"
                        f"🔢 *Please tell me the quantity you need* (e.g. *100* or *250 units*)."
                    )
                    self._finalize_reply(conv_id, IntentType.SELECT_PRODUCT.value, reply)
                    return reply

        # Fast-Path E1: Standalone Quantity for Selected Product or Active Quote
        # Handles "100", "what about 50?", "how much for 200?", "for 50 pieces", "200 units", etc.
        active_sku = conv.selected_sku or (conv.pending_quote.get("sku") if conv.pending_quote else None)
        if active_sku:
            standalone_qty = self._extract_standalone_quantity(clean_text)
            if standalone_qty is not None:
                reply = self._handle_direct_quote(conv_id, customer_phone, active_sku, standalone_qty)
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

        # Fast-Path G: Pending-quote contextual guidance ("what next", "how to proceed", etc.)
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

    def _handle_show_more(self, conv: Any, customer_phone: str) -> str:
        """
        Handles pagination continuation for product searches deterministically.
        Preserves original search query, category, budget constraints, and avoids repeating SKUs.
        """
        conv_id = conv.conversation_id
        clean_phone = customer_phone.lstrip("+").strip()
        ctx = self._get_search_context(clean_phone, conv)

        if not ctx or not conv.current_product_candidates:
            reply = (
                "I haven't searched for any products yet! 🎁\n\n"
                "What kind of corporate gifts are you looking for? For example:\n"
                "• *Gift sets under 500*\n"
                "• *Water bottles*\n"
                "• Reply *categories* to browse all collections"
            )
            self._finalize_reply(conv_id, IntentType.GENERAL_HELP.value, reply)
            return reply

        query = ctx.get("query")
        category = ctx.get("category")
        max_price = ctx.get("max_price")
        all_shown = list(ctx.get("all_shown_skus") or [])
        page = ctx.get("page", 1)
        target_qty = ctx.get("target_qty") or conv.selected_quantity

        # Query all matches from catalogue
        all_matches = catalogue_service.search_products(
            query=query,
            category=category,
            max_price=max_price,
            limit=500,
        )

        if not all_matches and category:
            all_matches = catalogue_service.search_products(category=category, limit=500)

        # Exclude already shown SKUs
        remaining = [p for p in all_matches if p.get("sku") and p.get("sku") not in all_shown]

        if not remaining:
            cat_name = category or "matching"
            reply = (
                f"I've shown all the {cat_name} options I found. "
                "You can select one of the products above (e.g. *1* to *5*), "
                "change the budget/category, or search for something else!"
            )
            self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
            return reply

        next_batch = remaining[:5]
        new_shown = all_shown + [p.get("sku") for p in next_batch]

        # Embed search metadata into next_batch items
        for item in next_batch:
            item["_search_query"] = query
            item["_search_category"] = category
            item["_search_max_price"] = max_price
            item["_all_shown_skus"] = new_shown
            item["_search_page"] = page + 1
            item["_target_qty"] = target_qty

        # Update in-memory search context
        ctx["all_shown_skus"] = new_shown
        ctx["page"] = page + 1
        self._search_contexts[clean_phone] = ctx

        reply = self._present_product_candidates(
            conv_id,
            customer_phone,
            next_batch,
            category_title=category,
            quantity=target_qty,
            is_continuation=True,
            max_price=max_price,
        )
        self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
        return reply

    def _handle_change_product(self, conv: Any, customer_phone: str) -> str:
        """Handles requests to change product or view alternative candidates."""
        conv_id = conv.conversation_id
        conversation_service.set_selected_product(conv_id, None)
        order_service.clear_pending_quote(customer_phone)

        if conv.current_product_candidates:
            clean_phone = customer_phone.lstrip("+").strip()
            ctx = self._get_search_context(clean_phone, conv)
            cat = ctx.get("category") if ctx else None
            qty = ctx.get("target_qty") if ctx else conv.selected_quantity
            reply = self._present_product_candidates(
                conv_id, customer_phone, conv.current_product_candidates,
                category_title=cat, quantity=qty, is_continuation=False
            )
            self._finalize_reply(conv_id, IntentType.PRODUCT_SEARCH.value, reply)
            return reply

        reply = (
            "Sure! Reply with *categories* to browse all collections, "
            "or tell me what products you are looking for (e.g. *gift sets under 500*)."
        )
        self._finalize_reply(conv_id, "SHOW_CATEGORIES", reply)
        return reply

    def _present_product_candidates(
        self,
        conv_id: str,
        customer_phone: str,
        candidates: List[Dict[str, Any]],
        category_title: Optional[str] = None,
        quantity: Optional[int] = None,
        is_continuation: bool = False,
        max_price: Optional[float] = None,
    ) -> str:
        """
        Stores candidates, queues optional rich media cards for WhatsApp,
        and formats the complete candidate list in ONE ordered WhatsApp message.
        Guarantees ordered items (1..5) followed by selection prompt.
        """
        if not candidates:
            title_disp = category_title or "that category"
            return (
                f"🔍 *No products currently found in \"{title_disp}\".*\n\n"
                "Reply with *Categories* to explore all available collections."
            )

        conversation_service.invalidate_quote_context(conv_id, customer_phone)
        conversation_service.set_candidates(conv_id, candidates)
        target_qty = quantity or 100
        if quantity:
            conversation_service.set_selected_quantity(conv_id, quantity)

        clean_phone = customer_phone.lstrip("+").strip()

        # Build media list for optional image dispatch
        media_list = self._build_candidate_media_messages(candidates, target_qty=target_qty)
        self._pending_media_messages[clean_phone] = media_list

        emoji_badges = {
            1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
            6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
        }

        price_str = f" under ₹{int(max_price)}" if max_price else ""
        if is_continuation:
            cat_display = category_title or "options"
            header = f"Sure — here are {len(candidates)} more {cat_display}{price_str}:"
        else:
            cat_display = category_title or "products"
            header = f"🎁 *Here are {len(candidates)} {cat_display}{price_str} matching your requirement:*\n_Found {len(candidates)} matching products_"

        items_formatted = []
        for idx, item in enumerate(candidates, 1):
            sku = item.get("sku", "N/A")
            cat = item.get("category", "")
            subcat = item.get("subcategory", "")
            if item.get("name"):
                prod_name = item["name"]
            elif subcat and subcat != cat:
                prod_name = f"{cat} – {subcat}"
            else:
                prod_name = cat or "Corporate Gift"

            badge = emoji_badges.get(idx, f"{idx}️⃣")
            card_lines = [
                f"{badge} *{prod_name}*",
                f"   🏷️ SKU: `{sku}`",
            ]

            # Pricing indicator
            if quantity and quantity > 0:
                try:
                    quote = pricing_service.calculate_total(sku, quantity)
                    if quote.available and quote.unit_price_excl_gst:
                        card_lines.append(
                            f"   💰 For {quantity} units: ₹{quote.unit_price_excl_gst:,.2f}/unit + {quote.gst_percentage:.0f}% GST\n"
                            f"   💰 Total: ₹{quote.total_price_incl_gst:,.2f} incl. GST"
                        )
                    else:
                        card_lines.append("   💰 Ask for quantity-based quote")
                except Exception:
                    card_lines.append("   💰 Ask for quantity-based quote")
            else:
                card_lines.append("   💰 Ask for quantity-based quote")

            colors = ", ".join(item.get("colors") or [])
            if colors:
                card_lines.append(f"   🎨 Options: {colors}")

            items_formatted.append("\n".join(card_lines))

        count = len(candidates)
        if count == 1:
            footer = "👉 Reply with 1 to select this product.\n👉 Or reply \"show more\" for more options."
        elif count == 2:
            footer = "👉 Reply with 1 or 2 to select a product.\n👉 Or reply \"show more\" for more options."
        else:
            footer = f"👉 Reply with 1–{count} to select a product.\n👉 Or reply \"show more\" for more options."

        return f"{header}\n\n" + "\n\n".join(items_formatted) + f"\n\n{footer}"

    def _build_candidate_media_messages(
        self,
        candidates: List[Dict[str, Any]],
        target_qty: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Builds rich WhatsApp image media items with concise captions for candidates with real image URLs.
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
                    prod_name = f"{cat} – {subcat}"
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
        message_text: str = "",
        image_sku: Optional[str] = None,
    ) -> str:
        """
        Handles requests to view product images/photos.
        Supports:
          1. Contextual SKU: explicit image_sku OR candidate index (e.g. 'I need image for 3') OR conv.selected_sku OR conv.pending_quote["sku"]
          2. Multiple candidates: when product list is displayed and no specific product selected.
          3. General prompt: when no product context exists.
        Never invents image URLs. Reports clearly when no image is available without falling into generic catalogue search.
        Does NOT change the active order/quote/confirmation state.
        """
        conv_id = conv.conversation_id
        cand_idx = extract_candidate_index_from_image_request(message_text) if message_text else None

        # Determine image target:
        # 1. Explicit SKU in message (e.g. 'image GS-002', 'photo of XG-501')
        # 2. Explicit candidate index (e.g. 'image 3', 'show image for 2')
        # 3. Active candidates list displayed: inspect all active candidates (do NOT fall back to old quote)
        # 4. Single product/quote context: only when no active candidates list is displayed
        if image_sku:
            target_sku = image_sku
            is_single_sku = True
        elif cand_idx and conv.current_product_candidates and 1 <= cand_idx <= len(conv.current_product_candidates):
            target_candidate = conv.current_product_candidates[cand_idx - 1]
            target_sku = target_candidate.get("sku")
            is_single_sku = True
        elif conv.selected_sku:
            target_sku = conv.selected_sku
            is_single_sku = True
        elif conv.current_product_candidates:
            target_sku = None
            is_single_sku = False
        else:
            target_sku = conv.pending_quote.get("sku") if conv.pending_quote else None
            is_single_sku = bool(target_sku)

        # --- Contextual or Explicit SKU Resolution ---
        if is_single_sku and target_sku:
            product = catalogue_service.get_by_sku(target_sku)
            canonical_sku = product.get("sku", target_sku) if product else target_sku
            image_url = catalogue_service.get_image_url(canonical_sku)
            if image_url:
                prod_name = ""
                if product:
                    cat = product.get("category", "")
                    subcat = product.get("subcategory", "")
                    prod_name = product.get("name") or (
                        f"{cat} — {subcat}" if subcat and subcat != cat else cat
                    )
                clean_phone = customer_phone.lstrip("+").strip()
                caption = (f"📦 *{prod_name}* ({canonical_sku})" if prod_name
                           else f"📦 {canonical_sku}")
                self._pending_media_messages[clean_phone] = [{
                    "sku": canonical_sku,
                    "image_url": image_url,
                    "caption": caption,
                }]
                if conv.pending_quote:
                    reply = (
                        f"✅ Image for {canonical_sku} is on its way!\n\n"
                        f"▪️ Reply *CONFIRM* to place this order, or enter a new quantity to update your quote."
                    )
                elif image_sku and not conv.selected_sku:
                    reply = (
                        f"✅ Image for {canonical_sku} is on its way!\n\n"
                        f"▪️ Reply with *{canonical_sku} 100* (or any quantity) to get an instant quote."
                    )
                else:
                    reply = (
                        f"✅ Image for {canonical_sku} is on its way!\n\n"
                        f"▪️ Reply with a quantity (e.g. *100*) to get an instant quote."
                    )
            else:
                if conv.pending_quote:
                    reply = (
                        f"❌ Sorry, I don’t have an image for {canonical_sku} yet.\n\n"
                        f"▪️ Reply *CONFIRM* to place this order, or enter a new quantity to update your quote."
                    )
                elif image_sku and not conv.selected_sku:
                    reply = (
                        f"❌ Sorry, I don’t have an image for {canonical_sku} yet.\n\n"
                        f"▪️ Reply with *{canonical_sku} 100* (or any quantity) to get an instant quote instead."
                    )
                else:
                    reply = (
                        f"❌ Sorry, I don’t have an image for {canonical_sku} yet.\n\n"
                        f"▪️ Reply with a quantity (e.g. *100*) to get an instant quote instead."
                    )
            self._finalize_reply(conv_id, "SHOW_IMAGES", reply)
            return reply

        candidates = conv.current_product_candidates

        # --- No context at all: ask which product ---
        if not candidates:
            reply = (
                "Sure — tell me the product or category you'd like to see images for "
                "(e.g. *Mugs*, *Gift Sets*, or *XG-501*).\n\n"
                "▪️ Reply with *Categories* to browse all collections."
            )
            self._finalize_reply(conv_id, IntentType.GENERAL_HELP.value, reply)
            return reply

        # --- Active candidates -- attempt to send images for all ---
        clean_phone = customer_phone.lstrip("+").strip()
        target_qty = conv.selected_quantity or 100
        media_list = self._build_candidate_media_messages(candidates, target_qty=target_qty)
        self._pending_media_messages[clean_phone] = media_list

        with_img_skus = {m["sku"] for m in media_list}
        missing_images = [
            (idx, c) for idx, c in enumerate(candidates, 1)
            if c.get("sku") not in with_img_skus
        ]

        if not media_list:
            cat_name = candidates[0].get("category", "these items") if candidates else "these items"
            sku_list = ", ".join(f"`{c.get('sku', '')}`" for c in candidates)
            reply = (
                f"📷 Photos are not currently on file for these {cat_name} ({sku_list}).\n\n"
                f"Which product would you like to see? Reply with the number or SKU."
            )
        elif len(media_list) == len(candidates):
            if len(candidates) == 1:
                reply = "▪️ Which product would you like to see? Reply with 1 to select this product."
            elif len(candidates) == 2:
                reply = "▪️ Which product would you like to see? Reply with 1 or 2 to select a product."
            else:
                _nums = ", ".join(str(i) for i in range(1, len(candidates))) + f", or {len(candidates)}"
                reply = f"▪️ Which product would you like to see? Reply with {_nums} to select a product."
        elif media_list:
            missing_lines = []
            for idx, c in missing_images:
                sku = c.get("sku", "")
                name = c.get("name") or c.get("category", "Product")
                missing_lines.append(f"{idx}️⃣ *{name}* (SKU: {sku}) — _Photo not available_")
            nums_str = ", ".join(str(i) for i in range(1, len(candidates))) + f", or {len(candidates)}"
            reply = (
                "📷 Photos sent for the products above.\n\n"
                + "\n".join(missing_lines) + "\n\n"
                + f"▪️ Which product would you like to see? Reply with {nums_str} or SKU."
            )
        else:
            sku_list = ", ".join(f"`{c.get('sku', '')}`" for c in candidates)
            reply = (
                f"📷 Photos are not currently on file for these items ({sku_list}).\n\n"
                "Which product would you like to see? Reply with the number or SKU."
            )

        self._finalize_reply(conv_id, "SHOW_IMAGES", reply)
        return reply

    @staticmethod
    def _extract_selection_index(text: str) -> Optional[int]:
        """Extracts 1-based selection index from numeric or ordinal text (supports 1..20)."""
        if not text:
            return None
        clean = text.strip().lower()
        for ch in ['*', '_', '~', '`', '"', "'", chr(8220), chr(8221), chr(8216), chr(8217)]:
            clean = clean.replace(ch, '')

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

    def _handle_multi_product_request(
        self,
        conv_id: str,
        customer_phone: str,
        multi_reqs: List[Dict[str, Any]],
    ) -> str:
        """
        Processes multi-product requirements (e.g. "I need 5 bootles and 10 pens").
        Searches each requirement independently, assigns candidate target quantities,
        and presents all options in an organized numbered layout.
        """
        clean_phone = customer_phone.lstrip("+").strip()
        all_candidates: List[Dict[str, Any]] = []
        req_results: List[Dict[str, Any]] = []

        for req in multi_reqs:
            cat = req.get("category", "")
            qty = req.get("quantity", 100)
            raw_term = req.get("raw_term", "")

            # Search by category first, fallback to query
            cands = catalogue_service.search_products(category=cat, limit=3)
            if not cands and raw_term:
                cands = catalogue_service.search_products(query=raw_term, limit=3)

            for c in cands:
                c["_target_qty"] = qty
                c["_req_category"] = cat

            req_results.append({
                "category": cat or raw_term.title(),
                "quantity": qty,
                "candidates": cands,
            })
            all_candidates.extend(cands)

        # Store all candidates in conversation context
        conversation_service.set_candidates(conv_id, all_candidates)
        all_skus = [c.get("sku") for c in all_candidates if c.get("sku")]
        for c in all_candidates:
            c["_all_shown_skus"] = all_skus
            c["_search_page"] = 1

        self._search_contexts[clean_phone] = {
            "query": None,
            "category": "Multi-Product Requirement",
            "max_price": None,
            "all_shown_skus": all_skus,
            "page": 1,
            "target_qty": None,
        }

        # Build media list for optional image dispatch
        media_list = self._build_candidate_media_messages(all_candidates, target_qty=100)
        self._pending_media_messages[clean_phone] = media_list

        emoji_badges = {
            1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
            6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"
        }

        lines = ["📋 *Here are the available options for your requirements:*\n"]
        overall_idx = 1

        for req_info in req_results:
            cat_name = req_info["category"]
            qty = req_info["quantity"]
            cands = req_info["candidates"]

            lines.append(f"📦 *{cat_name}* (Requirement: *{qty} units*):")
            if not cands:
                lines.append("   _No matching products found in catalogue._\n")
                continue

            for item in cands:
                sku = item.get("sku", "N/A")
                cat = item.get("category", "")
                subcat = item.get("subcategory", "")
                if item.get("name"):
                    prod_name = item["name"]
                elif subcat and subcat != cat:
                    prod_name = f"{cat} – {subcat}"
                else:
                    prod_name = cat or "Corporate Gift"

                badge = emoji_badges.get(overall_idx, f"{overall_idx}️⃣")
                item_lines = [f"{badge} *{prod_name}* (SKU: `{sku}`)"]

                try:
                    quote = pricing_service.calculate_total(sku, qty)
                    if quote.available and quote.unit_price_excl_gst:
                        item_lines.append(
                            f"   💰 For {qty} units: ₹{quote.unit_price_excl_gst:,.2f}/unit + {quote.gst_percentage:.0f}% GST"
                        )
                except Exception:
                    pass

                lines.append("\n".join(item_lines))
                overall_idx += 1
            lines.append("")

        lines.append(
            "▪️ Reply with a number (e.g. *1* or *3*) or SKU to view product details, request photos, or customize your quote."
        )
        return "\n".join(lines).strip()

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
        product = catalogue_service.get_by_sku(sku)
        canonical_sku = product.get("sku", sku) if product else sku
        product_name = None
        if product:
            cat = product.get("category", "")
            subcat = product.get("subcategory", "")
            product_name = f"{cat} – {subcat}" if subcat and subcat != cat else cat

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
        conv = conversation_service.get_conversation(conv_id)
        if not conv or not getattr(conv, "awaiting_confirmation", False) or not getattr(conv, "active_quote_id", None):
            return (
                "⚠️ You don't have an active quotation pending confirmation.\n\n"
                "Please select a product and quantity first (e.g. *GS-002 100*) to get an instant quote!"
            )

        pending = order_service.get_pending_quote(customer_phone)
        if not pending:
            raw_quote = conv.pending_quote
            if raw_quote:
                try:
                    pending = PriceQuoteResult(**raw_quote)
                    order_service.set_pending_quote(customer_phone, pending)
                except Exception:
                    pending = None

        # Deterministic Final Confirmation Gate:
        # 1. Active quote must exist
        # 2. Quote must be available (priced)
        # 3. Quantity must be valid (> 0)
        # 4. SKU must be present
        # 5. Quote ID must match active conversation quote context
        # 6. Quote must not be already ordered
        if (
            pending
            and getattr(pending, "available", False)
            and getattr(pending, "quantity", 0) > 0
            and getattr(pending, "sku", None)
            and not getattr(pending, "is_ordered", False)
            and (not getattr(pending, "quote_id", None) or pending.quote_id == conv.active_quote_id)
        ):
            order = order_service.confirm_pending_order(
                customer_phone,
                customer_name=customer_name,
                quote_id=conv.active_quote_id,
            )
            if not order:
                return (
                    "⚠️ You don't have an active quotation pending confirmation.\n\n"
                    "Please select a product and quantity first (e.g. *GS-002 100*) to get an instant quote!"
                )
            conversation_service.clear_pending_quote(conv_id)
            conversation_service.clear_selection(conv_id)
            order_service.clear_pending_quote(customer_phone)
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
                "⚠️ You don't have an active quotation pending confirmation.\n\n"
                "Please select a product and quantity first (e.g. *GS-002 100*) to get an instant quote!"
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
        clean_phone = customer_phone.lstrip("+").strip()

        # ---------------------------------------------------------------------
        # INTENT: PRODUCT_SEARCH
        # ---------------------------------------------------------------------
        if intent.intent == IntentType.PRODUCT_SEARCH:
            conversation_service.invalidate_quote_context(conv_id, customer_phone)
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
                all_skus = [c.get("sku") for c in candidates if c.get("sku")]
                for c in candidates:
                    c["_search_query"] = intent.query
                    c["_search_category"] = intent.category
                    c["_search_max_price"] = intent.budget_per_unit
                    c["_all_shown_skus"] = all_skus
                    c["_search_page"] = 1
                    c["_target_qty"] = intent.quantity
                self._search_contexts[clean_phone] = {
                    "query": intent.query,
                    "category": intent.category,
                    "max_price": intent.budget_per_unit,
                    "all_shown_skus": all_skus,
                    "page": 1,
                    "target_qty": intent.quantity,
                }
                return self._present_product_candidates(
                    conv_id,
                    customer_phone,
                    candidates,
                    category_title=intent.category,
                    quantity=intent.quantity,
                    max_price=intent.budget_per_unit,
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
                    subcat = candidate.get("subcategory")
                    name = candidate.get("name") or (f"{cat} – {subcat}" if subcat and subcat != cat else cat)
                    colors = candidate.get("colors")
                    color_str = f"\n🎨 *Options:* {', '.join(colors)}" if colors else ""
                    return (
                        f"Great choice! You selected: 🎁\n\n"
                        f"📦 *Product:* {name}\n"
                        f"🏷️ *SKU:* `{sku}`\n"
                        f"📂 *Category:* {cat}{color_str}\n\n"
                        f"Would you like a quote?\n"
                        f"🔢 *Please tell me the quantity you need* (e.g. *100* or *250 units*)."
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
                return f"🔢 How many units of *`{target_sku}`* do you need? (e.g. *100*, *250*)"
            elif not target_sku and target_qty:
                conversation_service.set_selected_quantity(conv_id, target_qty)
                return f"Got it, {target_qty} units! Which product code or category are you interested in? (e.g. *XG-GS-501* or *gift sets*)"
            else:
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
            return "🗑️ Your active quotation and selections have been cleared. Let me know if you would like to explore anything else!"

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
            # 1. Active pending quote: give pending quote guidance
            if conv.pending_quote:
                return self._pending_quote_guidance(conv)

            # 2. Active selected product: ask for quantity for that product
            if conv.selected_sku:
                prod = catalogue_service.get_by_sku(conv.selected_sku)
                name = prod.get("name") if prod else conv.selected_sku
                return (
                    f"You have selected *{name}* (`{conv.selected_sku}`).\n\n"
                    f"🔢 *Please tell me the quantity you need* (e.g. *100* or *250 units*) for an instant quotation.\n\n"
                    f"Or reply *show more* or *categories* to explore other options."
                )

            # 3. Active product candidates: guide selection or pagination
            if conv.current_product_candidates:
                count = len(conv.current_product_candidates)
                nums_str = "1" if count == 1 else f"1–{count}"
                return (
                    f"I'm here to help! You can:\n"
                    f"👉 Reply with a number ({nums_str}) to select one of the products above\n"
                    f"👉 Reply *show more* to see additional options\n"
                    f"👉 Reply *categories* to explore all collections"
                )

            # 4. Genuinely new or idle conversation welcome message
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

        # Fallback for UNKNOWN: Delegate to deterministic catalogue discovery first
        cat_reply = catalogue_service.resolve_customer_intent(message_text)
        if cat_reply:
            return cat_reply

        # If not catalogue-related, check active conversation state
        if conv.pending_quote:
            return self._pending_quote_guidance(conv)
        if conv.selected_sku:
            prod = catalogue_service.get_by_sku(conv.selected_sku)
            name = prod.get("name") if prod else conv.selected_sku
            return (
                f"You have selected *{name}* (`{conv.selected_sku}`).\n\n"
                f"🔢 *Please tell me the quantity you need* (e.g. *100* or *250 units*) for an instant quotation.\n\n"
                f"Or reply *show more* or *categories* to explore other options."
            )
        if conv.current_product_candidates:
            count = len(conv.current_product_candidates)
            nums_str = "1" if count == 1 else f"1–{count}"
            return (
                f"I'm here to help! You can:\n"
                f"👉 Reply with a number ({nums_str}) to select one of the products above\n"
                f"👉 Reply *show more* to see additional options\n"
                f"👉 Reply *categories* to explore all collections"
            )

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
        unit suffixes ('50 units', '100 pcs', '50 pieces', 'qty 50', 'need 100'),
        natural language typos ('50 unitys', '50 unita', '50 uni', '50 qty'),
        and natural questions ('what about 50?', 'how much for 200?', 'price for 200 instead?').
        """
        if not text:
            return None
        clean = text.strip()

        # Unit-word pattern includes common typos: unitys, unitas, uniti, qty variations
        _UNIT_WORDS = r"(?:units?|unitys?|unitas?|uniti|qty|quantity|pcs?|pieces?|nos?|items?|sets?)"

        patterns = [
            # "what about 50" / "make it 200" / "how much for 100" / "change to 250"
            rf"^(?:what\s+about|how\s+about|what\s+if|how\s+much\s+for|price\s+for|quote\s+for|"
            rf"can\s+you\s+give\s+(?:me\s+)?(?:the\s+)?price\s+for|make\s+it|change\s+to|for)"
            rf"\s+(\d+)\s*{_UNIT_WORDS}?(?:\s+instead|\s+please)?\s*[?.]*$",

            # "i need 50" / "i need 50 unitys" / "i want 50" / "give me 50" / "just 50"
            rf"^(?:i\s+(?:need|want|would\s+like|ll\s+take|take)|give\s+me|just|only|send\s+me)"
            rf"\s+(\d+)\s*{_UNIT_WORDS}?(?:\s+instead|\s+please)?\s*[.,?!]*$",

            # "50" / "50 units" / "50 unitys" / "qty 50" / "need 50" / "100 pcs" / "around 200"
            rf"^\s*[.,#\s]*(?:qty|quantity|need|for|just|around|about)?\s*[:\-]?\s*(\d+)"
            rf"\s*{_UNIT_WORDS}?(?:\s+instead|\s+please)?\s*[.,?!]*$",

            # "qty is 50" / "quantity: 100"
            r"\b(?:qty|quantity)\s*(?:is|=|:)?\s*(\d+)\b",

            # "50 instead"
            r"^(\d+)\s+instead\s*[?.]*$",
        ]
        for p in patterns:
            m = re.search(p, clean, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 100000:
                    return val
        return None

    @staticmethod
    def _is_next_step_question(text: str) -> bool:
        """
        Deterministically detects 'what next' / 'how to proceed' style questions.
        Used to avoid Gemini overhead and guarantee correct pending-quote context.
        """
        clean = re.sub(r'[^\w\s]', '', text.strip().lower()).strip()

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
        qty_str = f" for {qty} units" if qty else ""

        return (
            f"You have an active quote for *`{sku}`*{qty_str}.\n\n"
            "Here is how you can proceed:\n"
            "• Reply *CONFIRM* to place your order\n"
            "• Send a different number to *change quantity* (e.g. *100* or *what about 200*)\n"
            "• Reply *change product* or *show more* to explore other options\n"
            "• Reply *cancel* to clear this quote"
        )


# Singleton instance for production use
agent_router = AgentRouter()
