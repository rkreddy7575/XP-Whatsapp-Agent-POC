import json
import os
import re
from typing import List, Dict, Any, Optional

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue_master.json")

class CatalogueService:
    def __init__(self, data_path: Optional[str] = None):
        self.data_path = os.path.abspath(data_path or DATA_PATH)
        self.products: List[Dict[str, Any]] = []
        self.sku_index: Dict[str, Dict[str, Any]] = {}
        self.category_index: Dict[str, List[Dict[str, Any]]] = {}
        self._load_catalogue()

    def _load_catalogue(self):
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"Catalogue file not found at: {self.data_path}")

        with open(self.data_path, "r", encoding="utf-8") as f:
            self.products = json.load(f)

        self.sku_index.clear()
        self.category_index.clear()

        for item in self.products:
            sku = item.get("sku")
            if sku:
                # Normalise SKU: upper-case and strip
                norm_sku = sku.strip().upper()
                self.sku_index[norm_sku] = item
                # Strip punctuation and spaces
                clean_sku = re.sub(r'[^A-Z0-9]', '', norm_sku)
                if clean_sku:
                    self.sku_index[clean_sku] = item
                # Index prefix alternates (e.g. GS-002 <-> XG-GS-002)
                if norm_sku.startswith("XG-") or norm_sku.startswith("XG"):
                    without_xg = re.sub(r'^XG\s*-?\s*', '', norm_sku)
                    self.sku_index[without_xg] = item
                    self.sku_index[re.sub(r'[^A-Z0-9]', '', without_xg)] = item
                else:
                    with_xg = f"XG-{norm_sku}"
                    self.sku_index[with_xg] = item
                    self.sku_index[re.sub(r'[^A-Z0-9]', '', with_xg)] = item

            cat = item.get("category")
            if cat:
                cat_lower = cat.strip().lower()
                if cat_lower not in self.category_index:
                    self.category_index[cat_lower] = []
                self.category_index[cat_lower].append(item)

    @property
    def total_count(self) -> int:
        return len(self.products)

    def get_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        if not sku:
            return None
        norm_sku = sku.strip().upper()
        if norm_sku in self.sku_index:
            return self.sku_index[norm_sku]
        clean_sku = re.sub(r'[^A-Z0-9]', '', norm_sku)
        if clean_sku in self.sku_index:
            return self.sku_index[clean_sku]
        clean_no_xg = re.sub(r'^XG', '', clean_sku)
        if clean_no_xg in self.sku_index:
            return self.sku_index[clean_no_xg]
        return None

    def get_categories(self) -> List[Dict[str, Any]]:
        """Returns sorted list of distinct categories with item counts and subcategories."""
        cats: Dict[str, Dict[str, Any]] = {}
        for item in self.products:
            cat = item.get("category") or "Uncategorized"
            subcat = item.get("subcategory")
            if cat not in cats:
                cats[cat] = {"category": cat, "count": 0, "subcategories": set()}
            cats[cat]["count"] += 1
            if subcat:
                cats[cat]["subcategories"].add(subcat)

        result = []
        for cat, data in sorted(cats.items(), key=lambda x: x[0]):
            result.append({
                "category": cat,
                "count": data["count"],
                "subcategories": sorted(list(data["subcategories"]))
            })
        return result

    def search_products(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search products matching multiple criteria:
        - query: keyword matched against SKU, category, subcategory, colors
        - category: exact or case-insensitive category match
        - subcategory: exact or case-insensitive subcategory match
        - min_price / max_price: numeric range filter on source_price
        - limit: maximum results returned
        """
        results = []
        q_tokens = [t.lower() for t in re.split(r'\s+', query.strip()) if t] if query else []

        for item in self.products:
            # Category filter
            if category:
                item_cat = (item.get("category") or "").lower()
                if category.lower() not in item_cat:
                    continue

            # Subcategory filter
            if subcategory:
                item_subcat = (item.get("subcategory") or "").lower()
                if subcategory.lower() not in item_subcat:
                    continue

            # Price filters (only apply if source_price is numeric)
            price = item.get("source_price")
            if min_price is not None or max_price is not None:
                if not isinstance(price, (int, float)):
                    continue
                if min_price is not None and price < min_price:
                    continue
                if max_price is not None and price > max_price:
                    continue

            # Keyword search filter
            if q_tokens:
                sku_str = (item.get("sku") or "").lower()
                cat_str = (item.get("category") or "").lower()
                subcat_str = (item.get("subcategory") or "").lower()
                colors_str = " ".join(item.get("colors") or []).lower()

                # Full searchable text for this item
                searchable = f"{sku_str} {cat_str} {subcat_str} {colors_str}"
                # Every query token must appear in searchable text
                if not all(token in searchable for token in q_tokens):
                    continue

            results.append(item)
            if len(results) >= limit:
                break

        return results

    def format_product_card(self, product: Dict[str, Any]) -> str:
        """Formats a single product for customer-friendly display on WhatsApp."""
        sku = product.get("sku") or "N/A"
        category = product.get("category") or "Corporate Gift"
        subcategory = product.get("subcategory")
        colors = product.get("colors") or []
        price = product.get("source_price")

        lines = [f"🎁 *Product Code:* `{sku}`"]

        if subcategory and subcategory != category:
            lines.append(f"📂 *Category:* {category} — _{subcategory}_")
        else:
            lines.append(f"📂 *Category:* {category}")

        if colors:
            color_text = ", ".join(colors)
            lines.append(f"🎨 *Colors / Options:* {color_text}")
        else:
            lines.append("🎨 *Colors / Options:* Standard / Single Finish")

        image_url = product.get("image_url") or product.get("image")
        if image_url:
            lines.append(f"🖼️ *Image:* {image_url}")

        lines.append("💰 *Price:* Based on quantity")
        lines.append("📦 *Please tell me the quantity you need.*")

        lines.append("\n_Note: Reply with the product code and quantity (e.g., *XG-GS-501 100*) for an instant quotation._")
        return "\n".join(lines)

    def format_product_presentation(self, products: List[Dict[str, Any]]) -> str:
        """
        Formats product search options in a rich, WhatsApp-friendly visual format.
        Presents product name, canonical SKU, and product image (if available).
        Does not invent image URLs if unavailable.
        """
        if not products:
            return "🔍 *No products found matching your request.*"

        items_formatted = []
        for idx, item in enumerate(products, 1):
            sku = item.get("sku", "N/A")
            cat = item.get("category", "")
            subcat = item.get("subcategory", "")
            if item.get("name"):
                prod_name = item["name"]
            elif subcat and subcat != cat:
                prod_name = f"{cat} — {subcat}"
            else:
                prod_name = cat or "Corporate Gift"

            card_lines = [
                f"{idx}️⃣ *{prod_name}*",
                f"   🏷️ SKU: `{sku}`",
            ]

            image_url = item.get("image_url") or item.get("image")
            if image_url:
                card_lines.append(f"   🖼️ Image: {image_url}")

            colors = ", ".join(item.get("colors") or [])
            if colors:
                card_lines.append(f"   🎨 Options: {colors}")

            items_formatted.append("\n".join(card_lines))

        header = f"✨ *Found {len(products)} matching products:*\n"
        first_sku = products[0].get("sku", "GS-002") if products else "GS-002"
        footer = (
            f"\n👉 Reply with the item number (e.g. *1*, *2*, or *second one*) "
            f"or product code (e.g. *`{first_sku}`*) to select!"
        )
        return header + "\n" + "\n\n".join(items_formatted) + "\n" + footer

    def format_category_menu(self) -> str:
        """Formats the list of available categories for WhatsApp navigation."""
        categories = self.get_categories()
        lines = [
            "✨ *Mudhra Branding Solutions — Product Categories*\n",
            "Reply with a category name, product code, or budget to explore:\n"
        ]
        category_emojis = {
            "Gift Sets": "🎁",
            "Combos": "🗂️",
            "Notebooks": "📓",
            "Writing Instruments": "✒️",
            "Water Bottles": "🍶",
            "Keychains": "🔑",
            "Electronics": "🔋",
            "Mugs & Drinkware": "☕",
            "ID Cards & Accessories": "🪪",
            "Now Go": "✨"
        }

        for idx, cat_info in enumerate(categories, 1):
            name = cat_info["category"]
            count = cat_info["count"]
            emoji = category_emojis.get(name, "📦")
            lines.append(f"{idx}. {emoji} *{name}* ({count} items)")

        lines.append("\n💡 _Tip: You can also search by product code like `XG-501` or `GS-001`!_")
        return "\n".join(lines)

    def format_search_results(self, products: List[Dict[str, Any]], title: str = "Search Results") -> str:
        """Formats a list of search results for WhatsApp."""
        if not products:
            return "🔍 *No products found matching your request.*\n\nReply with *categories* to see available options, or try another search term."

        lines = [f"🔍 *{title}* (Found {len(products)} item{'s' if len(products) != 1 else ''}):\n"]

        for idx, p in enumerate(products, 1):
            sku = p.get("sku", "N/A")
            subcat = p.get("subcategory") or p.get("category", "")
            price = p.get("source_price")
            price_str = f"₹{price}" if price is not None else "Quote on request"
            colors = p.get("colors") or []
            color_str = f" | {', '.join(colors[:3])}" if colors else ""
            lines.append(f"{idx}. *`{sku}`* — {subcat} ({price_str}{color_str})")

        lines.append(f"\n💡 _Reply with any product code (e.g. *{products[0].get('sku')}*) for complete details!_")
        return "\n".join(lines)

    def resolve_customer_intent(self, text: Optional[str]) -> str:
        """
        Determines customer intent from incoming text message and returns the appropriate response:
        1. Empty/whitespace -> Helpful guidance prompt.
        2. SKU lookup (exact, lowercase, or punctuation-stripped).
        3. Categories command ("categories", "menu", etc.).
        4. Category request ("mugs", "bottles", "gift sets", "combos", etc.).
        5. Common greeting/help ("hi", "hello", "help").
        6. Keyword query search (e.g., "bamboo", "wooden", "leather").
        7. Unknown query fallback.
        """
        if not text or not text.strip():
            return (
                "👋 Welcome to *Mudhra Branding Solutions*!\n\n"
                "I can help you explore our corporate gifts catalogue:\n"
                "• Reply with *Categories* to browse all collections.\n"
                "• Try a category like *Mugs*, *Bottles*, *Gift Sets*, *Combos*, or *Pens*.\n"
                "• Or search with a product code like *XG-501* or *GS-001*."
            )

        clean_text = text.strip()
        lower_text = clean_text.lower()

        # 1. Check if input is a product SKU
        product = self.get_by_sku(clean_text)
        if product:
            return self.format_product_card(product)

        # 2. Check "categories" / "menu"
        if lower_text in [
            "categories", "category", "menu", "catalog",
            "catalogue", "all categories", "list categories"
        ]:
            return self.format_category_menu()

        # 3. Check Category Request
        category_aliases = {
            "mugs": "Mugs & Drinkware",
            "mug": "Mugs & Drinkware",
            "drinkware": "Mugs & Drinkware",
            "mugs & drinkware": "Mugs & Drinkware",
            "bottles": "Water Bottles",
            "bottle": "Water Bottles",
            "water bottles": "Water Bottles",
            "water bottle": "Water Bottles",
            "gift sets": "Gift Sets",
            "gift set": "Gift Sets",
            "gifts": "Gift Sets",
            "gift": "Gift Sets",
            "combos": "Combos",
            "combo": "Combos",
            "electronics": "Electronics",
            "electronic": "Electronics",
            "gadgets": "Electronics",
            "pens": "Writing Instruments",
            "pen": "Writing Instruments",
            "metal pens": "Writing Instruments",
            "writing instruments": "Writing Instruments",
            "notebooks": "Notebooks",
            "notebook": "Notebooks",
            "diaries": "Notebooks",
            "diary": "Notebooks",
            "keychains": "Keychains",
            "keychain": "Keychains",
            "key chains": "Keychains",
            "id cards": "ID Cards & Accessories",
            "id card": "ID Cards & Accessories",
            "now go": "Now Go",
        }

        matched_category = category_aliases.get(lower_text)
        if not matched_category:
            # Check direct category match (e.g. "Gift Sets")
            for cat in self.get_categories():
                if lower_text == cat["category"].lower():
                    matched_category = cat["category"]
                    break

        if matched_category:
            cat_products = self.search_products(category=matched_category, limit=6)
            return self.format_search_results(
                cat_products,
                title=f"{matched_category} Collection"
            )

        # 4. Check Common Greeting / Help
        if lower_text in ["hi", "hello", "hey", "help", "start", "good morning", "good afternoon", "good evening"]:
            return (
                "Hi! 👋 Welcome to *Mudhra Branding Solutions* — Corporate Gifting Specialist.\n\n"
                "How can I help you today?\n"
                "• Reply with *Categories* to browse all collections.\n"
                "• Type a category like *Mugs*, *Bottles*, *Gift Sets*, *Combos*, or *Pens*.\n"
                "• Search with a product code like *XG-501* or *GS-001*.\n"
                "• Search by keyword (e.g., *Bamboo*, *Wooden*, *Speaker*)."
            )

        # 5. Keyword search in catalogue
        search_results = self.search_products(query=clean_text, limit=6)
        if search_results:
            return self.format_search_results(
                search_results,
                title=f"Results for '{clean_text}'"
            )

        # 6. Fallback for unknown search terms
        return (
            f"🔍 *No products found matching \"{clean_text}\".*\n\n"
            "Here are some ways to explore our catalogue:\n"
            "• Reply with *Categories* to see all available categories.\n"
            "• Try a category like *Mugs*, *Bottles*, *Gift Sets*, *Combos*, or *Pens*.\n"
            "• Or search with a product code like *XG-501* or *GS-001*."
        )


# Singleton instance for simple importing
catalogue_service = CatalogueService()
