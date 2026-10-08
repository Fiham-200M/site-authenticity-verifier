"""Unit tests for classification_rules.py page-type classification engine."""

import unittest
from classification_rules import (
    classify_page_type,
    PAGE_TYPE_AMP,
    PAGE_TYPE_LANDING_PAGE,
    PAGE_TYPE_MONEY_SITE,
    PAGE_TYPE_NORMAL_PAGE,
)


class TestPageTypeClassification(unittest.TestCase):
    def test_amp_technical_declaration(self):
        signals = {
            "hasAmpAttr": True,
            "hasAmpBoilerplate": True,
            "hasAmpScript": True,
            "allElementsCount": 104,
            "totalVisibleImages": 6,
            "navLinksCount": 2,
            "gameCardsCount": 0,
            "navCategories": [],
            "detectedCategories": [],
            "detectedProviders": [],
            "internalLinksCount": 4,
            "totalLinksCount": 6,
            "textLength": 500,
            "pCount": 3,
            "detectedAmpCtas": ["LOGIN", "DAFTAR"],
        }
        ptype, reason, _ = classify_page_type(signals)
        self.assertEqual(ptype, PAGE_TYPE_AMP)
        self.assertIn("Technical AMP declared", reason)

    def test_promotional_landing_page(self):
        signals = {
            "hasAmpAttr": False,
            "hasAmpBoilerplate": False,
            "hasAmpScript": False,
            "ampHtmlHref": "https://login.example.com",
            "hasHeroBanner": True,
            "hasPromotionalContent": True,
            "hasFaqSection": True,
            "hasTrustPaymentBadges": True,
            "hasReviewsOrRatings": True,
            "hasMasuk": True,
            "hasDaftar": True,
            "gameCardsCount": 0,
            "categoryControlsCount": 0,
            "providerControlsCount": 0,
            "navCategories": [],
            "detectedCategories": [],
            "detectedProviders": [],
            "linkedImageTilesCount": 11,
            "internalLinksCount": 96,
            "totalLinksCount": 103,
            "allElementsCount": 990,
            "textLength": 7500,
            "pCount": 19,
        }
        ptype, reason, _ = classify_page_type(signals)
        self.assertEqual(ptype, PAGE_TYPE_LANDING_PAGE)
        self.assertIn("Promotional/conversion page", reason)

    def test_genuine_money_site(self):
        signals = {
            "hasAmpAttr": False,
            "hasAmpBoilerplate": False,
            "hasAmpScript": False,
            "gameCardsCount": 202,
            "categoryControlsCount": 8,
            "providerControlsCount": 12,
            "navCategories": ["hot games", "slots", "live casino"],
            "detectedCategories": ["hot games", "slots", "live casino"],
            "detectedProviders": ["pragmatic play", "pg soft", "habanero"],
            "linkedImageTilesCount": 108,
            "internalLinksCount": 273,
            "totalLinksCount": 440,
            "hasRotatingBanner": True,
            "hasMasuk": True,
            "hasDaftar": True,
            "allElementsCount": 3761,
            "totalVisibleImages": 275,
            "textLength": 12900,
            "pCount": 63,
        }
        ptype, reason, _ = classify_page_type(signals)
        self.assertEqual(ptype, PAGE_TYPE_MONEY_SITE)
        self.assertIn("Full-featured portal", reason)

    def test_normal_page_fallback(self):
        signals = {
            "hasAmpAttr": False,
            "hasAmpBoilerplate": False,
            "hasAmpScript": False,
            "gameCardsCount": 0,
            "categoryControlsCount": 0,
            "providerControlsCount": 0,
            "navCategories": [],
            "detectedCategories": [],
            "detectedProviders": [],
            "linkedImageTilesCount": 0,
            "internalLinksCount": 3,
            "totalLinksCount": 5,
            "hasRotatingBanner": False,
            "hasHeroBanner": False,
            "hasPromotionalContent": False,
            "hasFaqSection": False,
            "hasTrustPaymentBadges": False,
            "hasReviewsOrRatings": False,
            "hasMasuk": False,
            "hasDaftar": False,
            "allElementsCount": 80,
            "totalVisibleImages": 1,
            "textLength": 400,
            "pCount": 2,
        }
        ptype, reason, _ = classify_page_type(signals)
        self.assertEqual(ptype, PAGE_TYPE_NORMAL_PAGE)


if __name__ == "__main__":
    unittest.main()
