"""Content Intelligence Layer for end-link-lc crawler.

Analyzes text, titles, headings, CTAs, and semantic structure of crawled pages
independently from the logo verification pipeline. Classifies pages into
content categories (GAMBLING, CASINO, SPORTS_BETTING, REGISTRATION, etc.).

Designed with an extensible ContentAnalyzer interface so future NLP/LLM/VLM
models can be plugged in without changing crawler logic.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

# Standard content categories
CAT_GAMBLING = "GAMBLING"
CAT_CASINO = "CASINO"
CAT_SPORTS_BETTING = "SPORTS_BETTING"
CAT_REGISTRATION = "REGISTRATION"
CAT_LOGIN = "LOGIN"
CAT_PROMOTION = "PROMOTION"
CAT_BONUS = "BONUS"
CAT_PAYMENT = "PAYMENT"
CAT_LIVE_CHAT = "LIVE_CHAT"
CAT_AFFILIATE = "AFFILIATE"
CAT_LANDING_PAGE = "LANDING_PAGE"
CAT_AMP = "AMP"
CAT_MONEY_SITE = "MONEY_SITE"
CAT_OTHER = "OTHER"

# Keyword definitions with associated semantic weights
CATEGORY_TAXONOMY: Dict[str, Dict[str, Any]] = {
    CAT_CASINO: {
        "keywords": [
            "slot", "slots", "live casino", "roulette", "baccarat", "blackjack",
            "pragmatic", "pg soft", "habanero", "joker123", "spadegaming",
            "microgaming", "slot88", "sweet bonanza", "gates of olympus",
            "mahjong ways", "starlight princess", "tembak ikan", "dingdong",
        ],
        "weight": 2.0,
    },
    CAT_SPORTS_BETTING: {
        "keywords": [
            "judi bola", "taruhan bola", "sportsbook", "sbobet", "sbo", "cmd368",
            "parlay", "mix parlay", "handicap", "over under", "odds", "e-sports",
            "sabung ayam", "sv388", "lottery", "togel", "singapore pools",
        ],
        "weight": 2.0,
    },
    CAT_REGISTRATION: {
        "keywords": [
            "daftar sekarang", "daftar akun", "register now", "sign up", "buat akun",
            "registrasi", "form pendaftaran", "formulir daftar", "link daftar",
        ],
        "weight": 1.5,
    },
    CAT_LOGIN: {
        "keywords": [
            "login akun", "masuk akun", "sign in", "member login", "link alternatif login",
            "login sekarang", "silahkan login", "username", "password",
        ],
        "weight": 1.5,
    },
    CAT_PROMOTION: {
        "keywords": [
            "promo", "promosi", "event spesial", "turnamen", "hadiah", "reward",
            "cashback", "rollingan", "garansi kekalahan", "welcome promo",
        ],
        "weight": 1.2,
    },
    CAT_BONUS: {
        "keywords": [
            "bonus new member", "bonus deposit", "claim bonus", "klaim bonus",
            "bonus harian", "freebet", "bonus 100", "bonus 50", "to kecil",
            "turnover", "tanpa to", "extra bonus",
        ],
        "weight": 1.5,
    },
    CAT_PAYMENT: {
        "keywords": [
            "deposit", "withdraw", "penarikan dana", "qris", "dana", "ovo", "gopay",
            "linkaja", "bank bca", "bank mandiri", "bank bri", "bank bni", "pulsa tanpa potongan",
            "fast deposit", "transaksi 24 jam", "minimal deposit",
        ],
        "weight": 1.2,
    },
    CAT_LIVE_CHAT: {
        "keywords": [
            "live chat", "livechat", "hubungi kami", "customer service 24", "cs 24/7",
            "layanan bantuan", "whatsapp", "telegram", "mulai percakapan",
        ],
        "weight": 1.2,
    },
    CAT_AFFILIATE: {
        "keywords": [
            "referral", "afiliasi", "komisi referral", "ajak teman", "bonus referal",
            "downline", "link referral",
        ],
        "weight": 1.0,
    },
}


class ContentAnalyzer(ABC):
    """Abstract base class for content analysis and categorization engines."""

    @abstractmethod
    def analyze(self, page_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze page text, structure, and signals to extract content intelligence.

        Parameters
        ----------
        page_data : dict
            Contains 'text', 'title', 'meta_description', 'url', 'headings',
            'buttons', 'links', and any DOM signals.

        Returns
        -------
        dict with keys:
            categories: List[str]
            primary_category: str
            confidence: float (0.0 to 1.0)
            keywords: List[str]
            signals: Dict[str, Any]
        """
        pass


class DeterministicRuleContentAnalyzer(ContentAnalyzer):
    """Deterministic, high-performance rule-based content classifier.

    Extracts matched keyword frequencies across distinct document zones (title,
    headings, buttons, body) with zone-specific weighting.
    """

    def __init__(self, taxonomy: Optional[Dict[str, Dict[str, Any]]] = None):
        self.taxonomy = taxonomy or CATEGORY_TAXONOMY

    def analyze(self, page_data: Dict[str, Any]) -> Dict[str, Any]:
        title = (page_data.get("title") or "").lower()
        text = (page_data.get("text") or "").lower()
        headings = " ".join(page_data.get("headings") or []).lower()
        buttons = " ".join(page_data.get("buttons") or []).lower()
        url = (page_data.get("url") or "").lower()
        page_type = page_data.get("page_type")

        # Zone-weighted scores
        category_scores: Dict[str, float] = {}
        category_keywords: Dict[str, List[str]] = {}
        all_matched_keywords: Set[str] = set()

        for category, config in self.taxonomy.items():
            keywords = config["keywords"]
            cat_weight = config.get("weight", 1.0)
            score = 0.0
            matched_here = []

            for kw in keywords:
                pattern = rf"\b{re.escape(kw)}\b"
                title_matches = len(re.findall(pattern, title))
                heading_matches = len(re.findall(pattern, headings))
                btn_matches = len(re.findall(pattern, buttons))
                url_matches = 1 if kw.replace(" ", "") in url else 0
                body_matches = len(re.findall(pattern, text))

                if title_matches or heading_matches or btn_matches or url_matches or body_matches:
                    kw_score = (
                        (title_matches * 3.0)
                        + (heading_matches * 2.0)
                        + (btn_matches * 1.5)
                        + (url_matches * 2.0)
                        + min(body_matches, 10) * 0.5
                    ) * cat_weight
                    score += kw_score
                    matched_here.append(kw)
                    all_matched_keywords.add(kw)

            if score > 0:
                category_scores[category] = round(score, 2)
                category_keywords[category] = matched_here

        # Macro-category synthesis: If Casino or Sports Betting triggers strongly, also tag GAMBLING
        if category_scores.get(CAT_CASINO, 0) > 3.0 or category_scores.get(CAT_SPORTS_BETTING, 0) > 3.0:
            category_scores[CAT_GAMBLING] = max(
                category_scores.get(CAT_GAMBLING, 0),
                (category_scores.get(CAT_CASINO, 0) + category_scores.get(CAT_SPORTS_BETTING, 0)) * 0.8,
            )

        # Integrate structural page_type if available
        if page_type:
            if page_type == CAT_AMP:
                category_scores[CAT_AMP] = 10.0
            elif page_type == CAT_LANDING_PAGE:
                category_scores[CAT_LANDING_PAGE] = 10.0
            elif page_type == CAT_MONEY_SITE:
                category_scores[CAT_MONEY_SITE] = 10.0

        sorted_cats = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
        detected_categories = [c[0] for c in sorted_cats if c[1] >= 2.0]

        if not detected_categories:
            detected_categories = [CAT_OTHER]
            primary_cat = CAT_OTHER
            confidence = 0.0
        else:
            primary_cat = sorted_cats[0][0]
            top_score = sorted_cats[0][1]
            # Normalization to 0.0 - 1.0 confidence
            confidence = min(1.0, round(top_score / 25.0, 2))

        return {
            "categories": detected_categories,
            "primary_category": primary_cat,
            "confidence": confidence,
            "keywords": sorted(list(all_matched_keywords))[:25],
            "signals": {
                "category_scores": category_scores,
                "text_length": len(text),
                "title_analyzed": bool(title),
                "matched_keyword_count": len(all_matched_keywords),
            },
        }


# Module default singleton
_DEFAULT_ANALYZER = DeterministicRuleContentAnalyzer()


def analyze_page_content(page_data: Dict[str, Any], analyzer: Optional[ContentAnalyzer] = None) -> Dict[str, Any]:
    """Convenience function to analyze page content with the default or custom analyzer."""
    active_analyzer = analyzer or _DEFAULT_ANALYZER
    return active_analyzer.analyze(page_data)
