"""Unit tests for brand detection and normalization in classification_rules.py."""

import unittest
from classification_rules import (
    normalize_brand_text,
    detect_brands_in_text,
)


class TestBrandDetection(unittest.TestCase):
    def setUp(self):
        self.brands = ["SURGA77", "SGCWIN", "DEWI11", "MADURA88", "A200M"]

    def test_exact_brand_match(self):
        text = "Selamat datang di situs resmi SURGA77 online terbesar."
        dominant, counts, details = detect_brands_in_text(text, self.brands)
        self.assertEqual(dominant, "SURGA77")
        self.assertEqual(counts.get("SURGA77"), 1)
        self.assertEqual(details["SURGA77"]["match_method"], "exact")
        self.assertEqual(details["SURGA77"]["similarity"], 1.0)

    def test_accent_normalization(self):
        # Accented characters: SÚRGA77 -> SURGA77
        text = "Kunjungi portal SÚRGA77 sekarang juga."
        dominant, counts, details = detect_brands_in_text(text, self.brands)
        self.assertEqual(dominant, "SURGA77")
        self.assertGreaterEqual(counts.get("SURGA77", 0), 1)

    def test_cyrillic_homoglyph_detection(self):
        # Cyrillic 'А' (U+0410) instead of Latin 'A': SURGА77
        cyrillic_variant = "SURG\u043077"  # Cyrillic small 'а'
        text = f"Pendaftaran akun resmi di {cyrillic_variant} gacor."
        dominant, counts, details = detect_brands_in_text(text, self.brands)
        self.assertEqual(dominant, "SURGA77")
        self.assertIn("SURGA77", details)
        self.assertEqual(details["SURGA77"]["match_method"], "homoglyph")

    def test_conservative_fuzzy_match(self):
        # Typo or variant: SURGA76 or SGCW1N
        text = "Situs alternatif SGCW1N link login."
        norm_b = normalize_brand_text("SGCWIN")
        self.assertEqual(norm_b, "sgcwin")

        # Fuzzy test on small typographical deviation:
        text_typo = "Link alternatif SURGA78 resmi."
        dominant, counts, details = detect_brands_in_text(text_typo, ["SURGA77"], fuzzy_threshold=0.85)
        self.assertEqual(dominant, "SURGA77")
        self.assertEqual(details["SURGA77"]["match_method"], "fuzzy")
        self.assertGreater(details["SURGA77"]["similarity"], 0.85)

    def test_no_brand_detected(self):
        text = "Ini adalah portal berita umum tanpa ada kaitan sama sekali."
        dominant, counts, details = detect_brands_in_text(text, self.brands)
        self.assertIsNone(dominant)
        self.assertEqual(counts, {})


if __name__ == "__main__":
    unittest.main()
