"""Unit tests for crawler.py hardening, CLI compatibility, and cloaking logic."""

import unittest
from unittest.mock import MagicMock
from crawler import (
    parse_args,
    VerificationDecision,
    check_cloaking_divergence,
    url_key,
    eligible_url,
)


class TestCrawlerHardening(unittest.TestCase):
    def test_verification_decision_boolean_and_dict(self):
        # Must evaluate to True when positive
        vd_pos = VerificationDecision(True, {"verdict": "MATCH", "brand": "SURGA77", "confidence": 0.95})
        self.assertTrue(bool(vd_pos))
        if vd_pos:
            evaluated = True
        else:
            evaluated = False
        self.assertTrue(evaluated)
        self.assertEqual(vd_pos.get("brand"), "SURGA77")

        # Must evaluate to False when negative
        vd_neg = VerificationDecision(False, {"verdict": "UNKNOWN", "brand": None})
        self.assertFalse(bool(vd_neg))
        if vd_neg:
            evaluated = True
        else:
            evaluated = False
        self.assertFalse(evaluated)

    def test_cloaking_detection_on_closed_page(self):
        mock_page = MagicMock()
        mock_page.is_closed.return_value = True
        res = check_cloaking_divergence(mock_page, expected_brand="SURGA77")
        self.assertFalse(res["detected"])
        self.assertEqual(res["reason"], "Page closed")

    def test_url_key_normalization(self):
        k1 = url_key("https://EXAMPLE.com/path/?b=2&a=1")
        k2 = url_key("https://example.com/path/?b=2&a=1")
        self.assertEqual(k1, k2)

    def test_eligible_url_filtering(self):
        base = "https://example.com"
        # Video/archive extensions must not be crawled as web destinations
        self.assertIsNone(eligible_url("https://example.com/video.mp4", base))
        self.assertIsNone(eligible_url("https://example.com/archive.zip", base))
        # Regular HTML/navigation links must be eligible
        self.assertIsNotNone(eligible_url("https://example.com/login", base))

    def test_ai_logo_and_favicon_corroboration(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from crawler import ai_logo_verifier

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            logo_file = tmp_path / "logo.png"
            logo_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
            fav_file = tmp_path / "favicon.png"
            fav_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

            mock_logo_res = {
                "is_our_logo": True,
                "verdict": "MATCH",
                "brand": "SURGA11",
                "confidence": 0.95,
            }
            mock_fav_res = {
                "is_our_logo": True,
                "verdict": "MATCH",
                "brand": "SURGA11",
                "confidence": 0.91,
            }

            def side_effect(source, filename, asset_mode):
                if asset_mode == "logo":
                    return mock_logo_res
                return mock_fav_res

            with patch("logo_checker.check_image", side_effect=side_effect):
                decision = ai_logo_verifier("https://example.com", folder=tmp_dir, expected_brand="SURGA11")
                self.assertTrue(bool(decision))
                forensics = decision.forensics
                self.assertTrue(forensics.get("both_matched"))
                self.assertIsNotNone(forensics.get("logo"))
                self.assertIsNotNone(forensics.get("favicon"))
                self.assertEqual(forensics["logo"]["confidence"], 0.95)
                self.assertEqual(forensics["favicon"]["confidence"], 0.91)

    def test_logo_required_even_if_favicon_matches(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from crawler import ai_logo_verifier

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            logo_file = tmp_path / "logo.png"
            logo_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
            fav_file = tmp_path / "favicon.png"
            fav_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

            # Logo is UNKNOWN, favicon is MATCH
            mock_logo_res = {
                "is_our_logo": False,
                "verdict": "UNKNOWN",
                "brand": "UNKNOWN",
                "confidence": 0.0,
            }
            mock_fav_res = {
                "is_our_logo": True,
                "verdict": "MATCH",
                "brand": "TRI88",
                "confidence": 0.95,
            }

            def side_effect(source, filename, asset_mode):
                if asset_mode == "logo":
                    return mock_logo_res
                return mock_fav_res

            with patch("logo_checker.check_image", side_effect=side_effect):
                decision = ai_logo_verifier("https://example.com", folder=tmp_dir, expected_brand="TRI88")
                # When logo is not confirmed, decision must evaluate to False!
                self.assertFalse(bool(decision))

    def test_money_site_all_or_nothing_rules(self):
        # Helper to simulate Money Site decision logic
        def evaluate_money_site(logo_ours, fav_ours, chat_ours, logo_b, fav_b, crawl_b):
            brand_conflict = bool(
                (logo_ours and crawl_b and logo_b and logo_b != crawl_b.lower())
                or (fav_ours and crawl_b and fav_b and fav_b != crawl_b.lower())
                or (logo_ours and fav_ours and logo_b and fav_b and logo_b != fav_b)
            )
            all_ours = bool(logo_ours and fav_ours and chat_ours and not brand_conflict)
            if all_ours:
                return "OUR SITE"
            reasons = []
            if not logo_ours:
                reasons.append("LOGO MISMATCH")
            if not fav_ours:
                reasons.append("FAVICON MISMATCH")
            if not chat_ours:
                reasons.append("LIVE CHAT MISMATCH")
            if brand_conflict:
                reasons.append("BRAND CONFLICT")
            return f"{crawl_b.upper()} PHISHING ({', '.join(reasons)})"

        # Case 1: All verified authentic -> OUR SITE
        res1 = evaluate_money_site(True, True, True, "tri88", "tri88", "TRI88")
        self.assertEqual(res1, "OUR SITE")

        # Case 2: Favicon fails -> NOT OUR SITE (PHISHING)
        res2 = evaluate_money_site(True, False, True, "tri88", "unknown", "TRI88")
        self.assertEqual(res2, "TRI88 PHISHING (FAVICON MISMATCH)")

        # Case 3: Live Chat fails -> NOT OUR SITE (PHISHING)
        res3 = evaluate_money_site(True, True, False, "tri88", "tri88", "TRI88")
        self.assertEqual(res3, "TRI88 PHISHING (LIVE CHAT MISMATCH)")

        # Case 4: Logo fails -> NOT OUR SITE (PHISHING)
        res4 = evaluate_money_site(False, True, True, "unknown", "tri88", "TRI88")
        self.assertEqual(res4, "TRI88 PHISHING (LOGO MISMATCH)")

        # Case 5: Brand conflict between logo and favicon -> BRAND CONFLICT
        res5 = evaluate_money_site(True, True, True, "tri88", "surga11", "TRI88")
        self.assertEqual(res5, "TRI88 PHISHING (BRAND CONFLICT)")


if __name__ == "__main__":
    unittest.main()

