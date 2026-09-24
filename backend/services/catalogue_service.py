import sys
import json
import os
import re
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "catalogue_master.json")
IMAGE_MAP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "image_sku_map.json"))

def is_category_browsing_intent(text: str) -> bool:
    """
    Deterministically determines if input represents a category browsing or catalogue menu request.
    Handles variations, trailing punctuation, and WhatsApp markdown (*bold*, _italic_, quotes).
    """
    if not text:
        return False
    # Strip whitespace, quotes, markdown formatting characters (*, _, ~, `, quotes)
    clean = re.sub(r'[*_~`"\'\u201c\u201d\u2018\u2019]', '', text).strip().lower()
    clean = re.sub(r'[?!.,;:]+$', '', clean).strip()

    exact_matches = {
        'categories', 'category', 'menu', 'catalog', 'catalogue',
        'all categories', 'list categories', 'view categories',
        'browse categories', 'show categories', 'show me categories',
        'what categories do you have', 'what categories do u have',
        'what are your categories', 'what are the categories',
        'what categories are available', 'what categories are there',
        'what categories', 'which categories do you have',
        'which categories are available', 'show all categories',
        'browse collections', 'show collections', 'show me collections',
        'all collections', 'collections', 'collection', 'see categories',
        'categories list', 'category list', 'browse catalogue',
        'browse catalog', 'show catalogue', 'show catalog',
        'view catalogue', 'view catalog', 'our categories',
        'available categories', 'collections list', 'list collections',
    }
    if clean in exact_matches:
        return True

    patterns = [
        r'^(?:show|view|browse|list|get|see|display|tell\s+me)\s+(?:all\s+|the\s+|available\s+|our\s+|me\s+)*(?:categories|category|collections|catalogue|catalog)$',
        r'^(?:what|which)\s+(?:are\s+(?:the\s+|your\s+|available\s+)*)?(?:categories|collections)(?:\s+(?:do\s+(?:you|u)\s+have|are\s+there|are\s+available))?$',
        r'^(?:can\s+i\s+see|could\s+you\s+show\s+me|give\s+me)\s+(?:the\s+|all\s+|your\s+)*(?:categories|category|catalogue|catalog|collections)$',
    ]
    for pat in patterns:
        if re.match(pat, clean):
            return True
    return False


def is_image_request_intent(text: str) -> bool:
    """
    Deterministically determines if input represents a request to view images/photos/pictures
    of products. Handles natural variations, punctuation, and WhatsApp markdown (*bold*, _italic_, quotes).
    """
    if not text:
        return False
    # Strip whitespace, quotes, markdown formatting characters (*, _, ~, `, quotes)
    clean = re.sub(r'[*_~`"\'\u201c\u201d\u2018\u2019]', '', text).strip().lower()
    clean = re.sub(r'[?!.,;:]+$', '', clean).strip()

    exact_matches = {
        'image', 'images', 'photo', 'photos', 'pic', 'pics', 'picture', 'pictures',
        'product image', 'product images', 'product photo', 'product photos',
        'product picture', 'product pictures',
        'show image', 'show images', 'show photo', 'show photos', 'show pic', 'show pics',
        'show picture', 'show pictures', 'show me image', 'show me images',
        'show me photo', 'show me photos', 'show me pic', 'show me pics',
        'show me picture', 'show me pictures',
        'send image', 'send images', 'send photo', 'send photos', 'send pic', 'send pics',
        'send picture', 'send pictures', 'send me image', 'send me images',
        'send me photo', 'send me photos', 'send me pic', 'send me pics',
        'share image', 'share images', 'share photo', 'share photos', 'share pic', 'share pics',
        'view image', 'view images', 'view photo', 'view photos',
        'see image', 'see images', 'see photo', 'see photos', 'see picture', 'see pictures',
        'can i see', 'can i see them', 'can i see it',
        'can i see the images', 'can i see images', 'can i see photos', 'can i see the photos',
        'can i see picture', 'can i see pictures', 'can i see the picture', 'can i see the pictures',
        'can you show me image', 'can you show me images', 'can you show me photo', 'can you show me photos',
        'can you show me picture', 'can you show me pictures', 'can you show image', 'can you show images',
        'can you show the picture', 'can you show the pictures', 'can you show the image', 'can you show the images',
        'can you show picture', 'can you show pictures',
        'what does it look like', 'what do they look like', 'how does it look', 'how do they look',
        'do you have images', 'do you have photos', 'do you have pictures',
        'any images', 'any photos', 'any pictures',
    }
    if clean in exact_matches:
        return True

    patterns = [
        # can you show me images / could you please send photos / show product pictures / etc.
        r'^(?:can|could|please|pls|kindly)?\s*(?:you\s+|i\s+|we\s+)?\s*(?:please\s+|pls\s+|kindly\s+)?(?:show|send|share|see|view|display|provide|give)\s+(?:me\s+|us\s+)?(?:the\s+|some\s+|any\s+|all\s+|product\s+|products\s+|these\s+|those\s+)*(?:images?|photos?|pictures?|pics?)(?:\s+(?:please|pls))?$',
        # can I see them / can we view it / can I see the pictures
        r'^(?:can|could)\s+(?:i|we)\s+(?:see|view|look\s+at)\s+(?:them|it|these|those|(?:the\s+)?(?:images?|photos?|pictures?|pics?))(?:\s+(?:please|pls))?$',
        # what does it look like / how do they look
        r'^(?:what\s+does\s+it\s+look\s+like|what\s+do\s+they\s+look\s+like|how\s+does\s+it\s+look|how\s+do\s+they\s+look)$',
        # do you have any photos / are there any images
        r'^(?:do\s+you\s+have|are\s+there\s+any)\s+(?:any\s+)?(?:images?|photos?|pictures?|pics?)(?:\s+(?:for\s+(?:this|these|them))?)?$',
        # i want to see the photos
        r'^(?:i\s+want\s+to\s+see|i\s+would\s+like\s+to\s+see|want\s+to\s+see|would\s+like\s+to\s+see)\s+(?:the\s+|some\s+)?(?:images?|photos?|pictures?|pics?|them|it)$',
    ]
    for pat in patterns:
        if re.match(pat, clean):
            return True

    # Also match SKU-specific image requests: "image XG-MP-01", "photo of XG-MP-01", etc.
    # These are detected by extract_sku_from_image_request; is_image_request_intent returns True
    # so the caller routes to _handle_image_request in all cases.
    sku_patterns = [
        r'^(?:image|images|photo|photos|pic|pics|picture|pictures)\s+[a-z0-9][a-z0-9\-]{1,20}$',
        r'^(?:image|images|photo|photos|pic|pics|picture|pictures)\s+(?:of|for)\s+[a-z0-9][a-z0-9\-]{1,20}$',
        r'^(?:show|send|share|give|provide|display)\s+(?:me\s+|us\s+)?(?:image|images|photo|photos|pic|pics|picture|pictures)\s+(?:(?:for|of|on|about)\s+)?[a-z0-9][a-z0-9\-]{1,20}$',
        r'^(?:can|could)\s+(?:you\s+)?(?:please\s+)?(?:show|send|share|give|provide|display)\s+(?:me\s+|us\s+)?(?:the\s+|an?\s+)?(?:image|images|photo|photos|pic|pics|picture|pictures)\s+(?:(?:for|of|on|about)\s+)?[a-z0-9][a-z0-9\-]{1,20}$',
        r'^(?:show|send|share|give)\s+(?:me\s+)?[a-z0-9][a-z0-9\-]{1,20}\s+(?:image|images|photo|photos|pic|pics|picture|pictures)$',
    ]
    for pat in sku_patterns:
        if re.match(pat, clean, re.IGNORECASE):
            return True

    return False


def extract_sku_from_image_request(text: str) -> Optional[str]:
    """
    Extracts a product SKU from an image/photo request that includes a SKU code.
    Handles patterns such as:
      - "image XG-MP-01"
      - "photo of XG-MP-01"
      - "picture of XG-MP-01"
      - "show me image for XG-MP-01"
      - "can you show me image for XG-MP-01"
      - "send photo XG-MP-01"
    Returns the extracted SKU string (uppercased, normalized) or None.
    Never matches pure image words without a SKU.
    """
    if not text:
        return None
    clean = re.sub(r'[*_~`"\'\u201c\u201d\u2018\u2019]', '', text).strip().lower()
    clean = re.sub(r'[?!.,;:]+$', '', clean).strip()

    # Pattern: image/photo/picture/pic [of/for] <SKU>
    # Also: show [me] image/photo/picture [of/for] <SKU>
    # Also: can you show me image for <SKU>
    # SKU pattern: alphanumeric with hyphens, like XG-MP-01, XG-501, GS-002, MP-01
    sku_pattern = r'([a-z0-9][a-z0-9\-]{1,20})'
    image_words = r'(?:image|images|photo|photos|pic|pics|picture|pictures)'
    prefix_words = r'(?:can\s+(?:you\s+)?(?:please\s+)?)?(?:show|send|share|give|provide|display)?(?:\s+me|\s+us)?'
    connector = r'(?:\s+(?:for|of|on|about))?\s+'

    patterns = [
        # "image XG-MP-01" / "photo XG-MP-01" / "picture XG-MP-01"
        rf'^{image_words}\s+{sku_pattern}$',
        # "image of XG-MP-01" / "photo of XG-MP-01" / "picture for XG-MP-01"
        rf'^{image_words}\s+(?:of|for)\s+{sku_pattern}$',
        # "show image for XG-MP-01" / "send photo of XG-MP-01" / "show me picture of XG-MP-01"
        rf'^(?:show|send|share|give|provide|display)\s+(?:me\s+|us\s+)?{image_words}{connector}{sku_pattern}$',
        # "can you show me image for XG-MP-01"
        rf'^(?:can|could)\s+(?:you\s+)?(?:please\s+)?(?:show|send|share|give|provide|display)\s+(?:me\s+|us\s+)?(?:the\s+|an?\s+)?{image_words}{connector}{sku_pattern}$',
        # "show XG-MP-01 image" / "show XG-MP-01 photo"
        rf'^(?:show|send|share|give)\s+(?:me\s+)?{sku_pattern}\s+{image_words}$',
    ]

    for pat in patterns:
        m = re.match(pat, clean, re.IGNORECASE)
        if m:
            raw_sku = m.group(1).upper()
            # Must look like a real SKU (not a pure image word)
            if re.match(r'^[A-Z0-9][A-Z0-9\-]{1,20}$', raw_sku) and not raw_sku.lower() in {
                'image', 'images', 'photo', 'photos', 'pic', 'pics', 'picture', 'pictures', 'me', 'us', 'the', 'for', 'of'
            }:
                return raw_sku
    return None


class CatalogueService:
    def __init__(self, data_path: Optional[str] = None):
        self.data_path = os.path.abspath(data_path or DATA_PATH)
        self.products: List[Dict[str, Any]] = []
        self.sku_index: Dict[str, Dict[str, Any]] = {}
        self.category_index: Dict[str, List[Dict[str, Any]]] = {}
        self.image_sku_map: Dict[str, str] = {}
        if os.path.exists(IMAGE_MAP_PATH):
            try:
                with open(IMAGE_MAP_PATH, "r", encoding="utf-8") as img_f:
                    self.image_sku_map = json.load(img_f)
            except Exception:
                self.image_sku_map = {}
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

    def get_image_url(self, sku: str) -> Optional[str]:
        """
        Returns the public HTTPS Supabase Storage URL for an SKU if mapped.
        Never invents image URLs. Returns None if unmapped.
        """
        if not sku:
            return None
        norm_sku = sku.strip().upper()
        clean = re.sub(r"[^A-Z0-9]", "", norm_sku)

        # 1. Lookup in image_sku_map
        obj = self.image_sku_map.get(norm_sku) or self.image_sku_map.get(clean)
        if not obj:
            if norm_sku.startswith("XG-") or norm_sku.startswith("XG"):
                without_xg = re.sub(r"^XG\s*-?\s*", "", norm_sku)
                obj = self.image_sku_map.get(without_xg) or self.image_sku_map.get(re.sub(r"[^A-Z0-9]", "", without_xg))
            else:
                with_xg = f"XG-{norm_sku}"
                obj = self.image_sku_map.get(with_xg) or self.image_sku_map.get(re.sub(r"[^A-Z0-9]", "", with_xg))

        if obj:
            sb_url = os.getenv("SUPABASE_URL", "https://your-project.supabase.co").rstrip("/")
            return f"{sb_url}/storage/v1/object/public/product-images/{obj}"

        # 2. Check if product itself has an image_url
        prod = self.sku_index.get(norm_sku) or self.sku_index.get(clean)
        if prod and prod.get("image_url"):
            return prod["image_url"]

        return None

    @property
    def total_count(self) -> int:
        return len(self.products)

    def get_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        if not sku:
            return None
        # Supabase-first lookup if configured
        if not any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv) or os.getenv("USE_SUPABASE_IN_TESTS") in ("1", "true", "True"):
            try:
                from services.supabase_repository import SupabaseClient, SupabaseProductRepository
                sb = SupabaseClient()
                if sb.is_configured:
                    prod = SupabaseProductRepository(sb).get_by_sku(sku)
                    if prod:
                        return prod
            except Exception:
                pass
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
        # Defensive: Never search products for "categories" or image request keywords
        if query and (is_category_browsing_intent(query) or is_image_request_intent(query)):
            return []

        # Supabase-first search if configured
        if not any("unittest" in str(arg).lower() or "pytest" in str(arg).lower() for arg in sys.argv) or os.getenv("USE_SUPABASE_IN_TESTS") in ("1", "true", "True"):
            try:
                from services.supabase_repository import SupabaseClient, SupabaseProductRepository
                sb = SupabaseClient()
                if sb.is_configured:
                    sb_res = SupabaseProductRepository(sb).search_products(
                        query=query, category=category, max_price=max_price, limit=limit
                    )
                    if sb_res:
                        return sb_res
            except Exception:
                pass

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

    def format_product_presentation(self, products: List[Dict[str, Any]], quantity: Optional[int] = None) -> str:
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

            if quantity and quantity > 0:
                try:
                    from services.pricing_service import pricing_service
                    quote = pricing_service.calculate_total(sku, quantity)
                    if quote.available and quote.unit_price_excl_gst:
                        card_lines.append(
                            f"   💰 For {quantity} units: ₹{quote.unit_price_excl_gst:,.2f}/unit + {quote.gst_percentage:.0f}% GST\n"
                            f"   💵 Total: ₹{quote.total_price_incl_gst:,.2f} incl. GST"
                        )
                except Exception:
                    pass

            image_url = item.get("image_url") or item.get("image") or self.get_image_url(sku)
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

    def get_category_by_index(self, index: int) -> Optional[str]:
        """Returns category name by 1-based index from the sorted dynamic categories list."""
        cats = self.get_categories()
        if 1 <= index <= len(cats):
            return cats[index - 1]["category"]
        return None

    def match_category_name(self, text: str) -> Optional[str]:
        """
        Matches a category name or known alias against the catalogue categories.
        Returns the canonical category name or None.
        """
        if not text:
            return None
        clean = re.sub(r'[*_~`"\'\u201c\u201d\u2018\u2019]', '', text).strip().lower()
        clean = re.sub(r'[?!.,;:]+$', '', clean).strip()

        category_aliases = {
            "mugs": "Mugs & Drinkware",
            "mug": "Mugs & Drinkware",
            "drinkware": "Mugs & Drinkware",
            "mugs & drinkware": "Mugs & Drinkware",
            "mugs and drinkware": "Mugs & Drinkware",
            "bottles": "Water Bottles",
            "bottle": "Water Bottles",
            "water bottles": "Water Bottles",
            "water bottle": "Water Bottles",
            "gift sets": "Gift Sets",
            "gift set": "Gift Sets",
            "gifts": "Gift Sets",
            "gift": "Gift Sets",
            "giftsets": "Gift Sets",
            "combos": "Combos",
            "combo": "Combos",
            "electronics": "Electronics",
            "electronic": "Electronics",
            "gadgets": "Electronics",
            "pens": "Writing Instruments",
            "pen": "Writing Instruments",
            "metal pens": "Writing Instruments",
            "writing instruments": "Writing Instruments",
            "writing instrument": "Writing Instruments",
            "notebooks": "Notebooks",
            "notebook": "Notebooks",
            "diaries": "Notebooks",
            "diary": "Notebooks",
            "keychains": "Keychains",
            "keychain": "Keychains",
            "key chains": "Keychains",
            "key chain": "Keychains",
            "id cards": "ID Cards & Accessories",
            "id card": "ID Cards & Accessories",
            "id cards & accessories": "ID Cards & Accessories",
            "id cards and accessories": "ID Cards & Accessories",
            "now go": "Now Go",
            "nowgo": "Now Go",
        }

        if clean in category_aliases:
            return category_aliases[clean]

        for cat in self.get_categories():
            cat_name = cat["category"]
            if clean == cat_name.lower():
                return cat_name
            if clean == f"{cat_name.lower()} collection":
                return cat_name
            for sub in cat.get("subcategories", []):
                if clean == sub.lower():
                    return cat_name

        return None

    def format_category_menu(self) -> str:
        """Formats the list of available categories for WhatsApp navigation."""
        categories = self.get_categories()
        lines = [
            "✨ *Mudhra Branding Solutions — Product Categories*\n",
            "Reply with a number (e.g. *1*) or category name (e.g. *Gift Sets*) to view products:\n"
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
        if is_category_browsing_intent(clean_text):
            return self.format_category_menu()

        # 2b. Check "images" / "photos" request without active candidates
        if is_image_request_intent(clean_text):
            return (
                "Sure — tell me the product or category you'd like to see images for (e.g. *Mugs*, *Gift Sets*, or *XG-501*).\n\n"
                "• Reply with *Categories* to browse all collections."
            )

        # 3. Check Category Request
        matched_category = self.match_category_name(clean_text)
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
