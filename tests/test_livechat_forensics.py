"""Unit tests for livechat_verifier.py multi-provider forensics."""

import unittest
from livechat_verifier import (
    LiveChatInfo,
    LiveChatResult,
    _extract_from_html,
    _parse_from_script_text,
    format_livechat_output,
)


class TestLiveChatForensics(unittest.TestCase):
    def test_onechat_html_extraction(self):
        html = '<iframe src="https://chat.onechat.dev/index.html?id=0e7861ee-ff6c-4e35-a4df-75b0c096d78b&language=id"></iframe>'
        info = _extract_from_html(html)
        self.assertIsNotNone(info)
        self.assertEqual(info.chat_id, "0e7861ee-ff6c-4e35-a4df-75b0c096d78b")
        self.assertEqual(info.base_url, "https://chat.onechat.dev")
        self.assertIn("index.html?id=", info.constructed_url)

    def test_onechat_script_extraction(self):
        script = """
        window.WebSDK = {
            BASE_URL: "https://chat.onechat.dev",
            id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            language: "id"
        };
        """
        info = _parse_from_script_text(script)
        self.assertIsNotNone(info)
        self.assertEqual(info.chat_id, "a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    def test_livechat_result_structured_dictionary(self):
        info = LiveChatInfo(
            base_url="https://tawk.to",
            chat_id="60a1b2c3d4e5f67890123456",
            constructed_url="https://tawk.to/chat/60a1b2c3d4e5f67890123456",
            provider="tawk",
            account_id="60a1b2c3d4e5f67890123456",
            expected_account_id=None,
        )
        res = LiveChatResult(
            is_live_chat=False,
            status="UNKNOWN",
            live_chat_info=info,
            page_title="Support Chat",
            provider_evidence={
                "provider": "tawk",
                "detected": True,
                "account_id": "60a1b2c3d4e5f67890123456",
                "expected_account_id": None,
                "match": None,
                "evidence_source": "dom",
            },
        )
        d = res.to_dict()
        self.assertEqual(d["status"], "UNKNOWN")
        self.assertFalse(d["is_live_chat"])
        self.assertIn("provider_evidence", d)
        self.assertEqual(d["provider_evidence"]["provider"], "tawk")
        self.assertIsNone(d["provider_evidence"]["match"])

    def test_format_livechat_output_banner(self):
        info = LiveChatInfo(
            base_url="https://chat.onechat.dev",
            chat_id="12345-67890",
            constructed_url="https://chat.onechat.dev/index.html?id=12345-67890",
        )
        res = LiveChatResult(
            is_live_chat=True,
            status="VERIFIED",
            live_chat_info=info,
            page_title="Live Chat Support",
            brand_matched=True,
        )
        banner = format_livechat_output(info, res, colorize=False)
        self.assertIn("ACTUAL LIVE CHAT VERIFIED", banner)
        self.assertIn("12345-67890", banner)

    def test_widget_embed_html_onclick_extraction(self):
        html = '<a class="btn" href="javascript:void(0)" onclick="openLiveChat(\'https://srgchat.chat/widget-embed?token=cmovp9x5r000jlc07wpgfff29\' , \'\')">LIVE HELP</a>'
        info = _extract_from_html(html)
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "widget-embed")
        self.assertEqual(info.chat_id, "cmovp9x5r000jlc07wpgfff29")
        self.assertEqual(info.base_url, "https://srgchat.chat")
        self.assertEqual(info.constructed_url, "https://srgchat.chat/widget-embed?token=cmovp9x5r000jlc07wpgfff29")

    def test_widget_embed_direct_link_extraction(self):
        html = '<a href="https://srgchat.chat/widget-embed?token=cmovp9x5r000jlc07wpgfff29">Chat With Us</a>'
        info = _extract_from_html(html)
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "widget-embed")
        self.assertEqual(info.chat_id, "cmovp9x5r000jlc07wpgfff29")
        self.assertEqual(info.constructed_url, "https://srgchat.chat/widget-embed?token=cmovp9x5r000jlc07wpgfff29")

    def test_widget_embed_script_extraction(self):
        script = 'window.chatWidgetConfig = { embedUrl: "https://srgchat.chat/widget-embed?token=cmovp9x5r000jlc07wpgfff29" };'
        info = _parse_from_script_text(script)
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "widget-embed")
        self.assertEqual(info.chat_id, "cmovp9x5r000jlc07wpgfff29")
        self.assertEqual(info.constructed_url, "https://srgchat.chat/widget-embed?token=cmovp9x5r000jlc07wpgfff29")

    def test_direct_lc_chat_dewi11_extraction(self):
        html = '<a class="pointer" href="javascript:void(0)" onclick="openLiveChat(\'https://direct.lc.chat/18263412/4\' , \'\')">LIVE HELP</a>'
        info = _extract_from_html(html)
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "livechat")
        self.assertEqual(info.account_id, "18263412")
        self.assertEqual(info.chat_id, "18263412/4")
        self.assertEqual(info.constructed_url, "https://direct.lc.chat/18263412/4")

    def test_direct_lc_chat_jagoledak_extraction(self):
        html = '<a class="btn" href="https://direct.lc.chat/19353035/4">Live Chat Jagoledak</a>'
        info = _extract_from_html(html)
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "livechat")
        self.assertEqual(info.account_id, "19353035")
        self.assertEqual(info.chat_id, "19353035/4")
        self.assertEqual(info.constructed_url, "https://direct.lc.chat/19353035/4")

    def test_brand_livechat_config_loading(self):
        from livechat_verifier import load_brand_livechat_config
        cfg = load_brand_livechat_config()
        self.assertIn("DEWI11", cfg)
        self.assertIn("JAGOLEDAK", cfg)
        self.assertEqual(cfg["DEWI11"]["account_id"], "18263412")
        self.assertEqual(cfg["JAGOLEDAK"]["account_id"], "19353035")

    def test_brand_livechat_license_mismatch(self):
        from livechat_verifier import verify_live_chat_page
        info = LiveChatInfo(
            base_url="https://direct.lc.chat",
            chat_id="19353035/4",
            constructed_url="https://direct.lc.chat/19353035/4",
            provider="livechat",
            account_id="19353035",  # Jagoledak license
        )
        # Verify against DEWI11 (which expects 18263412) without needing browser network call
        res = verify_live_chat_page(None, info.constructed_url, expected_brand="DEWI11", live_chat_info=info)
        self.assertFalse(res.is_live_chat)
        self.assertEqual(res.status, "ACCOUNT MISMATCH")
        self.assertIn("mismatch", res.details.lower())

    def test_iframe_secure_livechatinc_dewi11_extraction(self):
        html = '<iframe src="https://secure.livechatinc.com/customer/action/open_chat?license_id=18263412&group=4&embedded=1&widget_version=3&unique_groups=0&organization_id=c096e748-d26e-4cc2-9ca8-4ad9d6a1094e&use_parent_storage=1&x-region=us-south1"></iframe>'
        info = _extract_from_html(html)
        self.assertIsNotNone(info)
        self.assertEqual(info.provider, "livechat")
        self.assertEqual(info.account_id, "18263412")
        self.assertEqual(info.chat_id, "18263412/4")
        self.assertEqual(info.constructed_url, "https://direct.lc.chat/18263412/4")



if __name__ == "__main__":
    unittest.main()


