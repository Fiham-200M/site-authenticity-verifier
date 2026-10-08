"""Unit tests for content_intelligence.py."""

import unittest
from content_intelligence import (
    analyze_page_content,
    CAT_CASINO,
    CAT_GAMBLING,
    CAT_SPORTS_BETTING,
    CAT_LOGIN,
    CAT_REGISTRATION,
    CAT_PROMOTION,
    CAT_BONUS,
    CAT_PAYMENT,
    CAT_LIVE_CHAT,
    CAT_OTHER,
)


class TestContentIntelligence(unittest.TestCase):
    def test_casino_and_gambling_detection(self):
        page_data = {
            "url": "https://example.com/slots",
            "title": "Situs Slot Online Pragmatic Play & Live Casino Terpercaya",
            "text": "Nikmati permainan slot online gacor gates of olympus, sweet bonanza, dan live casino roulette.",
            "headings": ["Slot Gacor Hari Ini", "Provider Pragmatic Play"],
            "buttons": ["Mainkan Sekarang", "Daftar Akun"],
        }
        res = analyze_page_content(page_data)
        self.assertIn(CAT_CASINO, res["categories"])
        self.assertIn(CAT_GAMBLING, res["categories"])
        self.assertGreater(res["confidence"], 0.4)

    def test_sports_betting_detection(self):
        page_data = {
            "url": "https://example.com/sportsbook",
            "title": "Judi Bola SBOBET & Taruhan Parlay Terlengkap",
            "text": "Pasang taruhan judi bola mix parlay dengan odds terbaik sportsbook sbobet.",
            "headings": ["Jadwal Pertandingan Bola"],
            "buttons": ["Pasang Taruhan"],
        }
        res = analyze_page_content(page_data)
        self.assertIn(CAT_SPORTS_BETTING, res["categories"])

    def test_login_and_registration_detection(self):
        page_data = {
            "url": "https://example.com/auth",
            "title": "Form Pendaftaran Akun Baru & Member Login",
            "text": "Daftar sekarang untuk membuat akun baru atau login akun menggunakan username dan password.",
            "headings": ["Registrasi Member Baru"],
            "buttons": ["Daftar Akun", "Masuk Akun"],
        }
        res = analyze_page_content(page_data)
        self.assertTrue(CAT_LOGIN in res["categories"] or CAT_REGISTRATION in res["categories"])

    def test_promotional_and_bonus_detection(self):
        page_data = {
            "url": "https://example.com/promo",
            "title": "Promosi & Bonus New Member 100%",
            "text": "Klaim bonus new member 100 dengan to kecil turnover mudah dan bonus deposit harian.",
            "headings": ["Penawaran Spesial Bonus"],
            "buttons": ["Klaim Bonus"],
        }
        res = analyze_page_content(page_data)
        self.assertTrue(CAT_PROMOTION in res["categories"] or CAT_BONUS in res["categories"])

    def test_payment_and_livechat_detection(self):
        page_data = {
            "url": "https://example.com/help",
            "title": "Layanan Bantuan Customer Service & Deposit QRIS",
            "text": "Deposit bank BCA, Mandiri, dan QRIS instan 24 jam. Hubungi customer service kami via live chat.",
            "headings": ["Metode Pembayaran"],
            "buttons": ["Mulai Percakapan Live Chat"],
        }
        res = analyze_page_content(page_data)
        self.assertTrue(CAT_PAYMENT in res["categories"] or CAT_LIVE_CHAT in res["categories"])

    def test_unrelated_content_defaults_to_other(self):
        page_data = {
            "url": "https://example.com/recipes",
            "title": "Delicious Apple Pie Recipe",
            "text": "Mix flour, butter, apples, and cinnamon in a bowl. Bake at 375 degrees for 45 minutes.",
            "headings": ["Ingredients", "Preparation Steps"],
            "buttons": ["Print Recipe"],
        }
        res = analyze_page_content(page_data)
        self.assertEqual(res["primary_category"], CAT_OTHER)
        self.assertEqual(res["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
