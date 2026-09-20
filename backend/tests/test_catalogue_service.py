import unittest
import os
import sys

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.catalogue_service import CatalogueService, catalogue_service

class TestCatalogueService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = catalogue_service

    def test_total_count(self):
        self.assertEqual(self.service.total_count, 768)

    def test_exact_sku_lookup(self):
        # Test GS-001
        p = self.service.get_by_sku("GS-001")
        self.assertIsNotNone(p)
        self.assertEqual(p["sku"], "GS-001")
        self.assertEqual(p["category"], "Gift Sets")
        self.assertEqual(p["subcategory"], "Gift Set (2 in 1)")
        self.assertEqual(p["source_price"], 140)

        # Test XG-501
        p = self.service.get_by_sku("XG-501")
        self.assertIsNotNone(p)
        self.assertEqual(p["sku"], "XG-501")
        self.assertEqual(p["category"], "Combos")
        self.assertIn("BL", p["colors"])
        self.assertEqual(p["source_price"], 260)

        # Test XG-MG-001
        p = self.service.get_by_sku("XG-MG-001")
        self.assertIsNotNone(p)
        self.assertEqual(p["category"], "Mugs & Drinkware")
        self.assertEqual(p["source_price"], 95)

    def test_case_insensitive_and_clean_sku_lookup(self):
        # Lowercase
        p1 = self.service.get_by_sku("gs-001")
        self.assertIsNotNone(p1)
        self.assertEqual(p1["sku"], "GS-001")

        # Hyphen stripped
        p2 = self.service.get_by_sku("xg501")
        self.assertIsNotNone(p2)
        self.assertEqual(p2["sku"], "XG-501")

        # Non-existent
        p3 = self.service.get_by_sku("NON_EXISTENT_SKU_123")
        self.assertIsNone(p3)

    def test_categories_listing(self):
        cats = self.service.get_categories()
        self.assertEqual(len(cats), 10)
        total_items = sum(c["count"] for c in cats)
        self.assertEqual(total_items, 768)

        cat_names = [c["category"] for c in cats]
        self.assertIn("Gift Sets", cat_names)
        self.assertIn("Combos", cat_names)
        self.assertIn("Water Bottles", cat_names)
        self.assertIn("Writing Instruments", cat_names)
        self.assertIn("Mugs & Drinkware", cat_names)

    def test_category_search(self):
        mugs = self.service.search_products(category="Mugs", limit=50)
        self.assertEqual(len(mugs), 46)
        for m in mugs:
            self.assertEqual(m["category"], "Mugs & Drinkware")

    def test_price_range_filtering(self):
        results = self.service.search_products(min_price=100, max_price=150, limit=20)
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIsInstance(r["source_price"], (int, float))
            self.assertGreaterEqual(r["source_price"], 100)
            self.assertLessEqual(r["source_price"], 150)

    def test_keyword_query(self):
        bamboo_items = self.service.search_products(query="Bamboo", limit=20)
        self.assertGreater(len(bamboo_items), 0)
        for item in bamboo_items:
            # bamboo must be in color, subcategory, or category
            searchable = (
                item.get("category", "") + " " +
                item.get("subcategory", "") + " " +
                " ".join(item.get("colors", []))
            ).lower()
            self.assertIn("bamboo", searchable)

    def test_formatting_functions(self):
        p = self.service.get_by_sku("XG-501")
        card = self.service.format_product_card(p)
        self.assertIn("XG-501", card)
        self.assertIn("Based on quantity", card)
        self.assertIn("Please tell me the quantity you need", card)

        menu = self.service.format_category_menu()
        self.assertIn("Gift Sets", menu)
        self.assertIn("Combos", menu)
        self.assertIn("Mudhra Branding Solutions", menu)

        search_output = self.service.format_search_results([p], title="Top Pick")
        self.assertIn("Top Pick", search_output)
        self.assertIn("XG-501", search_output)

if __name__ == "__main__":
    unittest.main()
