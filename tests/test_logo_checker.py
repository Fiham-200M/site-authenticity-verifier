"""Unit tests for logo_checker.py caching, error isolation, and API payload contracts."""

import unittest
from pathlib import Path
from logo_checker import (
    check_image,
    check_screenshot,
    get_cache_stats,
    clear_cache,
    _error_result,
    VERDICT_MATCH,
    VERDICT_UNKNOWN,
)


class TestLogoChecker(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_cache_deduplication(self):
        # 1x1 transparent PNG bytes
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
            b"\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        res1 = check_image(png_bytes, filename="test_logo.png", asset_mode="logo")
        stats1 = get_cache_stats()
        self.assertEqual(stats1["misses"], 1)
        self.assertEqual(stats1["hits"], 0)

        # Call again with the exact same image bytes
        res2 = check_image(png_bytes, filename="test_logo_duplicate.png", asset_mode="logo")
        stats2 = get_cache_stats()
        self.assertEqual(stats2["hits"], 1)
        self.assertTrue(res2.get("cached"))
        self.assertEqual(res1.get("sha256"), res2.get("sha256"))

    def test_error_result_structure(self):
        err = _error_result("Test timeout", error_code="AI_TIMEOUT", sha256="123456")
        self.assertFalse(err["is_our_logo"])
        self.assertEqual(err["verdict"], VERDICT_UNKNOWN)
        self.assertEqual(err["error"], "AI_TIMEOUT")
        self.assertEqual(err["sha256"], "123456")

    def test_invalid_source_error_handling(self):
        res = check_image("non_existent_file_path_12345.png")
        self.assertFalse(res["is_our_logo"])
        self.assertEqual(res["error"], "LOAD_ERROR")


if __name__ == "__main__":
    unittest.main()
