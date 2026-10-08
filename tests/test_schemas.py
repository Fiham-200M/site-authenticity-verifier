"""Unit tests for schemas.py ensuring Pydantic v2 validation and backward compatibility."""

import unittest
from schemas import (
    BrandMatch,
    LogoForensics,
    LiveChatForensics,
    ContentClassification,
    CloakingResult,
    PerformanceMetrics,
    PageRecord,
    CrawlReport,
)


class TestSchemas(unittest.TestCase):
    def test_brand_match_model(self):
        bm = BrandMatch(brand="SURGA77", match_method="homoglyph", similarity=1.0, occurrences=3)
        self.assertEqual(bm.brand, "SURGA77")
        self.assertEqual(bm.match_method, "homoglyph")
        self.assertEqual(bm.similarity, 1.0)
        self.assertEqual(bm.occurrences, 3)

    def test_logo_forensics_model(self):
        lf = LogoForensics(
            verdict="MATCH",
            brand_id="surga77",
            confidence=0.96,
            threat_type="NONE",
            siglip_score=0.97,
            dino_score=0.93,
            delta_e=2.1,
            edge_iou=0.88,
            ocr_match=True,
            cached=False,
            sha256="abcdef123456",
        )
        self.assertEqual(lf.verdict, "MATCH")
        self.assertEqual(lf.brand_id, "surga77")
        self.assertEqual(lf.siglip_score, 0.97)
        self.assertTrue(lf.ocr_match)

    def test_live_chat_forensics_model(self):
        lcf = LiveChatForensics(
            provider="tawk",
            detected=True,
            account_id="5a1b2c3d",
            expected_account_id="5a1b2c3d",
            match=True,
            evidence_source="script",
            status="VERIFIED",
        )
        self.assertEqual(lcf.provider, "tawk")
        self.assertTrue(lcf.match)

    def test_content_classification_model(self):
        cc = ContentClassification(
            categories=["CASINO", "GAMBLING"],
            primary_category="CASINO",
            confidence=0.85,
            keywords=["slot", "pragmatic"],
        )
        self.assertIn("CASINO", cc.categories)
        self.assertEqual(cc.primary_category, "CASINO")

    def test_page_record_backward_compatibility(self):
        pr = PageRecord(
            requested_url="https://example.com",
            url="https://example.com/home",
            folder="/path/to/folder",
            classification="OUR SITE",
            page_type="MONEY_SITE",
            page_type_reason="Catalog detected",
            links=["https://example.com/login"],
            url_counts={"https://example.com/login": 1},
            brand_matches=["SURGA77"],
            selected_brand="SURGA77",
            logo_identified=True,
            total_urls=1,
            total_url_occurrences=1,
        )
        report_dict = pr.to_report_dict()
        # Verify required legacy keys exist
        self.assertIn("requested_url", report_dict)
        self.assertIn("url", report_dict)
        self.assertIn("classification", report_dict)
        self.assertIn("page_type", report_dict)
        self.assertIn("logo_identified", report_dict)
        self.assertTrue(report_dict["logo_identified"])

    def test_crawl_report_backward_compatibility(self):
        cr = CrawlReport(
            start_url="https://example.com",
            brands=["SURGA77", "MADURA88"],
            classifier="manual",
            classification="OUR SITE",
            pages=[],
            redirects=[],
            errors=[],
            rebrandly_matches=[],
            status="COMPLETED",
        )
        data = cr.to_report_dict()
        self.assertEqual(data["start_url"], "https://example.com")
        self.assertEqual(data["classification"], "OUR SITE")
        self.assertEqual(data["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
