"""Website-type identification rules, DOM feature extraction, and classification engine.

This module separates page-type classification from crawler navigation logic.
Classifies pages into:
- LANDING_PAGE
- AMP
- MONEY_SITE
- NORMAL_PAGE
"""
import re
from urllib.parse import urljoin, urlparse
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page


# Page Type Constants
PAGE_TYPE_LANDING_PAGE = "LANDING_PAGE"
PAGE_TYPE_AMP = "AMP"
PAGE_TYPE_MONEY_SITE = "MONEY_SITE"
PAGE_TYPE_NORMAL_PAGE = "NORMAL_PAGE"

# Action & Category Keywords
GAMING_CATEGORIES = [
    "hot games", "slots", "slot", "live casino", "casino", "sports", "sportsbook",
    "arcade", "poker", "togel", "tembak ikan", "fishing", "crash game", "table game",
    "e-sports", "sabung ayam", "lottery"
]

GAME_PROVIDERS = [
    "pragmatic", "pragmatic play", "pg soft", "pgsoft", "habanero", "joker", "joker123",
    "spadegaming", "microgaming", "jili", "playtech", "nolimit city", "no limit city",
    "hacksaw", "hacksaw gaming", "slot88", "fastspin", "cq9", "red tiger", "netent",
    "sexy baccarat", "evolution gaming", "sbo", "sbobet"
]

AMP_CTA_KEYWORDS = [
    "daftar akun", "login utama", "link alternatif", "live chat",
    "login akun", "daftar sekarang", "login sekarang", "masuk akun",
    "daftar", "login", "masuk", "register", "sign in", "sign up",
]

MONEY_SITE_LABELS = [
    "masuk", "login", "daftar", "register", "live chat", "sign in", "sign up"
]

PROMOTIONAL_TERMS = [
    "promo", "promosi", "bonus", "garansi", "turnover", "to kecil",
    "cashback", "new member", "deposit", "withdraw", "keunggulan",
    "mengapa memilih", "layanan 24", "tanya jawab", "penawaran spesial",
    "maxwin", "gacor", "klaim bonus", "syarat dan ketentuan"
]


PAGE_TYPE_DOM_JS = r"""() => {
    try {
        const htmlEl = document.documentElement;
        const body = document.body;
        if (!body) return null;

        // 1. Technical AMP Indicators
        const hasAmpAttr = !!(htmlEl.hasAttribute('amp') || htmlEl.hasAttribute('⚡') ||
                              htmlEl.hasAttribute('data-ampdevmode'));
        const hasAmpBoilerplate = !!document.querySelector('style[amp-boilerplate], style[amp-custom]');
        const hasAmpScript = !!document.querySelector('script[src*="ampproject.org"], script[src*="amp.js"]');
        const ampCustomTags = document.querySelectorAll(
            'amp-img, amp-anim, amp-carousel, amp-pixel, amp-analytics, amp-sidebar, amp-accordion'
        ).length;
        const ampHtmlLink = document.querySelector('link[rel="amphtml"]');
        const ampHtmlHref = ampHtmlLink ? (ampHtmlLink.href || ampHtmlLink.getAttribute('href') || '') : '';
        const canonicalLink = document.querySelector('link[rel="canonical"]');
        const canonicalHref = canonicalLink ? (canonicalLink.href || canonicalLink.getAttribute('href') || '') : '';

        // 2. DOM metrics
        const allElementsCount = document.querySelectorAll('*').length;
        const bodyText = (body.innerText || '').trim();
        const textLength = bodyText.length;
        const wordCount = bodyText ? bodyText.split(/\\s+/).length : 0;
        const pCount = document.querySelectorAll('p').length;
        const h1Count = document.querySelectorAll('h1').length;
        const h2Count = document.querySelectorAll('h2').length;
        const h3Count = document.querySelectorAll('h3').length;

        // 3. Visuals / Images
        const isVisible = el => {
            try {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 15 && rect.height > 15 && style.display !== 'none' && style.visibility !== 'hidden';
            } catch(e) { return false; }
        };
        const regularImages = Array.from(document.querySelectorAll('img')).filter(isVisible);
        const ampImages = Array.from(document.querySelectorAll('amp-img')).filter(isVisible);
        const totalVisibleImages = regularImages.length + ampImages.length;

        // 4. Links and Navigation
        const allLinks = Array.from(document.querySelectorAll('a[href]'));
        const totalLinksCount = allLinks.length;
        const currentHost = (window.location.hostname || '').toLowerCase();
        let internalLinksCount = 0;
        let externalLinksCount = 0;
        for (const a of allLinks) {
            try {
                const u = new URL(a.href, window.location.href);
                if (u.hostname.toLowerCase() === currentHost) {
                    internalLinksCount++;
                } else if (u.protocol.startsWith('http')) {
                    externalLinksCount++;
                }
            } catch(e) {}
        }

        // Nav bars & menus
        const navElements = Array.from(document.querySelectorAll(
            'nav, [role="navigation"], .navbar, .top-menu, .menu-slide, .nav-menu, header nav, .nav-tabs, .category-menu'
        ));
        let navLinksCount = 0;
        const navLinksText = [];
        navElements.forEach(nav => {
            const links = nav.querySelectorAll('a');
            navLinksCount += links.length;
            links.forEach(l => {
                const t = (l.innerText || '').trim().toLowerCase();
                if (t) navLinksText.push(t);
            });
        });

        // 5. Buttons and Clickables
        const clickableEls = Array.from(document.querySelectorAll(
            'button, a.btn, a.button, [role="button"], .cyber-btn, .btn, input[type="button"], input[type="submit"], header a, .actions a'
        ));
        const buttonTexts = [];
        const buttonDetails = [];
        let externalCtaLinksCount = 0;
        for (const el of clickableEls) {
            try {
                const txt = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
                const rawHref = el.getAttribute('href') || el.getAttribute('data-href') || '';
                let isExternal = false;
                let resolvedHref = rawHref;
                if (rawHref && rawHref !== '#' && !rawHref.startsWith('javascript:')) {
                    try {
                        const u = new URL(rawHref, window.location.href);
                        resolvedHref = u.href;
                        if (u.protocol.startsWith('http')) {
                            const curHost = currentHost.replace(/^www\./, '');
                            const uHost = u.hostname.toLowerCase().replace(/^www\./, '');
                            if (uHost !== curHost && !uHost.endsWith('.' + curHost) && !curHost.endsWith('.' + uHost)) {
                                isExternal = true;
                            }
                        }
                    } catch(e) {}
                }
                if (txt) {
                    buttonTexts.push(txt.toUpperCase());
                    buttonDetails.push({ text: txt, href: resolvedHref, tag: el.tagName.toLowerCase(), isExternal });
                    if (isExternal && /login|daftar|masuk|register|join/i.test(txt)) {
                        externalCtaLinksCount++;
                    }
                }
            } catch(e) {}
        }

        const lowerText = bodyText.toLowerCase();

        // 6. Gaming categories
        const gamingCategories = [
            'hot games', 'slots', 'slot', 'live casino', 'casino', 'sports', 'sportsbook',
            'arcade', 'poker', 'togel', 'tembak ikan', 'fishing', 'crash game', 'table game',
            'e-sports', 'sabung ayam', 'lottery'
        ];
        const detectedCategories = [];
        const navCategories = [];
        for (const cat of gamingCategories) {
            const re = new RegExp('\\\\b' + cat.replace('-', '[- ]') + '\\\\b', 'i');
            if (navLinksText.some(t => re.test(t))) {
                navCategories.push(cat);
            }
            if (allLinks.some(a => re.test((a.innerText || '').toLowerCase())) || re.test(lowerText)) {
                detectedCategories.push(cat);
            }
        }

        // 7. Providers
        const providers = [
            'pragmatic', 'pragmatic play', 'pg soft', 'pgsoft', 'habanero', 'joker', 'joker123',
            'spadegaming', 'microgaming', 'jili', 'playtech', 'nolimit city', 'no limit city',
            'hacksaw', 'hacksaw gaming', 'slot88', 'fastspin', 'cq9', 'red tiger', 'netent',
            'sexy baccarat', 'evolution gaming', 'sbo', 'sbobet'
        ];
        const detectedProviders = [];
        const navProviders = [];
        const providerElements = Array.from(document.querySelectorAll('.provider, .providers, .vendor-item, .provider-tab, [data-provider]'));
        for (const prov of providers) {
            const re = new RegExp('\\\\b' + prov + '\\\\b', 'i');
            if (providerElements.some(el => re.test((el.innerText || el.getAttribute('data-provider') || '').toLowerCase()))) {
                navProviders.push(prov);
            }
            if (allLinks.some(a => re.test((a.innerText || '').toLowerCase())) || re.test(lowerText)) {
                detectedProviders.push(prov);
            }
        }

        // 8. Game / product cards (the primary Money Site fingerprint)
        const gameCardSelectors = [
            '.game-item', '.game-card', '.game-list-container li', '.games-container li',
            '[data-vendor-name]', '[data-game-code]', '.game-box', '.product-card',
            '.game-item-content', '.slot-item', '.games-list > *', '.game-list > *',
            '[class*="game-card"]', '[class*="game-item"]', '[class*="game_box"]',
            '[class*="game-list"] li', '[class*="game-grid"] > *'
        ].join(', ');
        const gameCardElements = Array.from(document.querySelectorAll(gameCardSelectors));
        const gameCardsCount = gameCardElements.length;
        let gameCardsWithPlayOrDemo = 0;
        gameCardElements.forEach(card => {
            const cardText = (card.innerText || '').toLowerCase();
            if (/\\b(mainkan|play now|play demo|coba|demo|play)\\b/i.test(cardText)) {
                gameCardsWithPlayOrDemo++;
            }
        });

        // Some portals render catalog entries as generic linked image tiles with
        // generated class names, so named game-card selectors alone miss them.
        const linkedImageTiles = Array.from(document.querySelectorAll('a[href]')).filter(a => {
            if (!isVisible(a) || !a.querySelector('img, picture, [style*="background-image"]')) return false;
            try {
                const u = new URL(a.href, window.location.href);
                return u.hostname.toLowerCase() === currentHost && !a.closest('header, footer');
            } catch(e) { return false; }
        });
        const linkedImageTilesCount = linkedImageTiles.length;

        const categoryControlSelectors = [
            '.category, .categories, .category-item, .category-tab, .game-category',
            '[class*="category-menu"], [class*="game-menu"], [class*="game-nav"]',
            '[data-category], [data-game-type]'
        ].join(', ');
        const categoryControlsCount = Array.from(document.querySelectorAll(categoryControlSelectors)).filter(isVisible).length;
        const providerControlsCount = providerElements.filter(isVisible).length;

        // Money sites commonly rotate several promotional banners. This remains
        // supporting evidence because a landing page can also contain a slider.
        const carouselRoots = Array.from(document.querySelectorAll(
            '.swiper, .swiper-container, .slick-slider, .owl-carousel, .carousel, .splide, .glide, ' +
            '[class*="carousel"], [class*="slideshow"], [class*="slider"], [data-slider]'
        )).filter(isVisible);
        const slideSelectors = '.swiper-slide, .slick-slide, .owl-item, .carousel-item, [class*="slide"]';
        let carouselSlidesCount = 0;
        let carouselImagesCount = 0;
        carouselRoots.forEach(root => {
            carouselSlidesCount = Math.max(carouselSlidesCount, root.querySelectorAll(slideSelectors).length);
            carouselImagesCount = Math.max(carouselImagesCount, root.querySelectorAll('img, picture').length);
        });
        const pageMarkup = document.documentElement.innerHTML;
        const hasCarouselMarkup = /babysite[_-]sliding[_-]banners|slide__content|swiper-slide|slick-slide|owl-carousel|carousel-item|splide__slide/i.test(pageMarkup);
        const hasRotatingBanner = hasCarouselMarkup || (carouselRoots.length > 0 &&
            (carouselSlidesCount >= 2 || carouselImagesCount >= 2 ||
             !!document.querySelector('.swiper-pagination, .slick-dots, .owl-dots, .carousel-indicators')));

        // 9. Interactive Money site widgets
        const hasRtp = /rtp\\s*[:\\s]?\\s*\\d+(\\.\\d+)?%/i.test(bodyText) || !!document.querySelector('.fixed-rtp, [class*="rtp"]');
        const hasPlayOrDemo = /\\b(mainkan|play now|play demo|coba gratis|demo play)\\b/i.test(bodyText);
        const hasGameSearch = !!document.querySelector(
            'input[placeholder*="cari game" i], input[placeholder*="search game" i], input[placeholder*="cari slot" i], .search_popup_button, .game-search, [data-action="search-game"]'
        );
        const hasJackpotWidget = !!document.querySelector('.jackpot-container, .home-progressive-jackpot, [class*="jackpot"]') ||
                                 /progressive\\s+jackpot/i.test(bodyText);
        // Only trigger withdrawal ticker for actual activity feeds (not promotional "minimal withdraw")
        const hasWithdrawalTicker = !!document.querySelector('marquee, .messagebleft-container, .running-text, .ticker, .recent-withdraw') ||
                                    (/(?:berhasil\\s+(?:wd|withdraw|penarikan)|transaksi\\s+terakhir|live\\s+withdraw)/i.test(bodyText) && !hasAmpAttr);

        // 10. Login / Register controls
        const combinedBtnText = buttonTexts.join(' ').toLowerCase();
        const hasMasuk = /\\b(masuk|login|sign in)\\b/i.test(combinedBtnText) || /\\b(masuk|login|sign in)\\b/i.test(lowerText.slice(0, 1000));
        const hasDaftar = /\\b(daftar|register|sign up)\\b/i.test(combinedBtnText) || /\\b(daftar|register|sign up)\\b/i.test(lowerText.slice(0, 1000));

        // 11. Logo hyperlink
        const logoLinks = [];
        const logoSelectors = 'a.logo, a#logo, a[class*="logo"], header a:has(img), .brand-logo-link, a[aria-label*="logo" i], a[aria-label*="beranda" i], a:has(img[alt*="logo" i])';
        document.querySelectorAll(logoSelectors).forEach(a => {
            if (a.href) logoLinks.push(a.href);
        });

        // 12. AMP CTA keywords
        const ampCtaPatterns = [/daftar\\s*akun/i, /login\\s*utama/i, /link\\s*alternatif/i, /live\\s*chat/i, /login\\s*akun/i, /^login$/i, /^daftar$/i, /^masuk$/i];
        const detectedAmpCtas = [];
        for (const b of buttonTexts) {
            for (const pat of ampCtaPatterns) {
                if (pat.test(b) && !detectedAmpCtas.includes(b)) {
                    detectedAmpCtas.push(b);
                }
            }
        }

        // 13. Landing Page promotional features
        const hasHeroBanner = !!document.querySelector('.hero, .banner, .hero-img, .hero-banner, .slider, [class*="hero"], [class*="banner"]') ||
                              (h1Count >= 1 && /\\b(promo|promosi|bonus|gacor|jackpot|maxwin|daftar|resmi|terpercaya|agen|situs|login)\\b/i.test(document.querySelector('h1')?.innerText || ''));
        const hasPromotionalContent = /\\b(promo|promosi|bonus|garansi|turnover|to kecil|cashback|new member|deposit|withdraw|keunggulan|mengapa memilih|layanan 24|tanya jawab|penawaran spesial|maxwin|gacor)\\b/i.test(lowerText);
        const hasFaqSection = !!document.querySelector('#faq, .faq, [class*="faq"], #faq-schema, [itemtype*="FAQPage"]') || /\\b(faq|tanya jawab|pertanyaan umum)\\b/i.test(lowerText);
        const hasTrustPaymentBadges = !!document.querySelector('.trust-badge, .payment-methods, .bank-logo, [class*="payment"], [class*="bank"]') ||
                                      /\\b(bca|mandiri|bni|bri|qris|dana|ovo|gopay|linkaja|cimb)\\b/i.test(lowerText);
        const hasReviewsOrRatings = !!document.querySelector('.rating, .review, .stars, [itemtype*="Review"], [itemtype*="Rating"]') ||
                                    /\\b(testimoni|review pengguna|ulasan|bintang 5|rating)\\b/i.test(lowerText);

        return {
            hasAmpAttr,
            hasAmpBoilerplate,
            hasAmpScript,
            ampCustomTags,
            ampHtmlHref,
            canonicalHref,
            allElementsCount,
            textLength,
            wordCount,
            pCount,
            h1Count,
            h2Count,
            h3Count,
            totalVisibleImages,
            totalLinksCount,
            internalLinksCount,
            externalLinksCount,
            navLinksCount,
            buttonTexts: buttonTexts.slice(0, 30),
            buttonDetails: buttonDetails.slice(0, 35),
            externalCtaLinksCount,
            detectedCategories,
            navCategories,
            detectedProviders,
            navProviders,
            gameCardsCount,
            gameCardsWithPlayOrDemo,
            linkedImageTilesCount,
            categoryControlsCount,
            providerControlsCount,
            hasRotatingBanner,
            hasCarouselMarkup,
            carouselSlidesCount,
            carouselImagesCount,
            hasRtp,
            hasPlayOrDemo,
            hasGameSearch,
            hasJackpotWidget,
            hasWithdrawalTicker,
            hasMasuk,
            hasDaftar,
            logoLinks: Array.from(new Set(logoLinks)),
            detectedAmpCtas,
            hasHeroBanner,
            hasPromotionalContent,
            hasFaqSection,
            hasTrustPaymentBadges,
            hasReviewsOrRatings
        };
    } catch(err) {
        return { error: String(err) };
    }
}"""


def classify_page_type(signals: dict) -> tuple[str, str, dict]:
    """Classify page type into LANDING_PAGE, AMP, MONEY_SITE, or NORMAL_PAGE."""
    if not signals or signals.get("error"):
        return PAGE_TYPE_NORMAL_PAGE, "No DOM signals extracted or evaluation failed", signals or {}

    # Technical AMP flags
    has_amp_tech = bool(
        signals.get("hasAmpAttr", False)
        or signals.get("hasAmpBoilerplate", False)
        or signals.get("hasAmpScript", False)
        or signals.get("ampCustomTags", 0) > 0
    )
    elem_count = signals.get("allElementsCount", 0)
    visible_imgs = signals.get("totalVisibleImages", 0)
    nav_links = signals.get("navLinksCount", 0)
    amp_ctas = signals.get("detectedAmpCtas", [])
    text_len = signals.get("textLength", 0)
    p_count = signals.get("pCount", 0)

    # Categories and providers
    detected_cats = signals.get("detectedCategories", [])
    nav_cats = signals.get("navCategories", [])
    cat_count = len(detected_cats)
    nav_cat_count = len(nav_cats)

    detected_provs = signals.get("detectedProviders", [])
    nav_provs = signals.get("navProviders", [])
    prov_count = len(detected_provs)
    nav_prov_count = len(nav_provs)

    # Game catalog / cards (Crucial differentiator between Money Site and Landing Page)
    game_cards = signals.get("gameCardsCount", 0)
    cards_with_play = signals.get("gameCardsWithPlayOrDemo", 0)
    linked_image_tiles = signals.get("linkedImageTilesCount", 0)
    category_controls = signals.get("categoryControlsCount", 0)
    provider_controls = signals.get("providerControlsCount", 0)
    has_rotating_banner = signals.get("hasRotatingBanner", False)
    hero_changed = signals.get("heroChanged", False)
    total_links = signals.get("totalLinksCount", 0)
    internal_links = signals.get("internalLinksCount", 0)
    has_play_demo = signals.get("hasPlayOrDemo", False)
    has_rtp = signals.get("hasRtp", False)
    has_game_search = signals.get("hasGameSearch", False)
    has_jackpot = signals.get("hasJackpotWidget", False)
    has_wd_ticker = signals.get("hasWithdrawalTicker", False)

    # Auth & CTA signals
    has_masuk = signals.get("hasMasuk", False)
    has_daftar = signals.get("hasDaftar", False)
    external_ctas = signals.get("externalCtaLinksCount", 0)

    # Promotional / Landing Page features
    has_hero = signals.get("hasHeroBanner", False)
    has_promo = signals.get("hasPromotionalContent", False)
    has_faq = signals.get("hasFaqSection", False)
    has_trust = signals.get("hasTrustPaymentBadges", False)
    has_reviews = signals.get("hasReviewsOrRatings", False)
    amp_html_href = signals.get("ampHtmlHref", "")

    # Calculate Money Site score
    money_score = 0
    # Dedicated gaming navigation tabs
    if nav_cat_count >= 3:
        money_score += 4 + nav_cat_count * 2
    elif nav_cat_count >= 1:
        money_score += 2

    # Provider catalog / selector tabs
    if nav_prov_count >= 2:
        money_score += 4 + nav_prov_count
    elif prov_count >= 3:
        money_score += 2

    # Repeated game cards is the hallmark of a Money Site
    if game_cards >= 10:
        money_score += 10 + min(game_cards // 4, 10)
    elif game_cards >= 5:
        money_score += 6
    elif game_cards >= 3 and (cards_with_play >= 1 or has_play_demo):
        money_score += 4

    if has_game_search:
        money_score += 3
    if has_jackpot:
        money_score += 3
    if has_wd_ticker:
        money_score += 3
    if has_rtp or has_play_demo:
        money_score += 2
    if (has_masuk or has_daftar) and (game_cards >= 3 or nav_cat_count >= 2):
        money_score += 2
    if linked_image_tiles >= 12:
        money_score += 8
    elif linked_image_tiles >= 6:
        money_score += 4
    if category_controls >= 4:
        money_score += 5
    elif category_controls >= 2:
        money_score += 3
    if provider_controls >= 4:
        money_score += 5
    elif provider_controls >= 2:
        money_score += 3
    if has_rotating_banner:
        money_score += 3
    if hero_changed:
        money_score += 4
    if internal_links >= 100:
        money_score += 8
    elif internal_links >= 40:
        money_score += 5
    elif internal_links >= 25:
        money_score += 3

    # Landing page core signals: requires promotional copy, funnel CTAs, or amphtml
    has_lp_core = bool(has_promo or external_ctas >= 1 or amp_html_href)

    # Calculate Landing Page score
    lp_score = 0
    if bool(amp_html_href):
        lp_score += 5
    if external_ctas >= 1:
        lp_score += 5  # Strong signal: CTA points to external AMP or Money Site funnel
    if has_hero and has_lp_core:
        lp_score += 4
    if has_promo:
        lp_score += 4
    if has_faq and has_lp_core:
        lp_score += 3
    if has_trust and has_lp_core:
        lp_score += 2
    if has_reviews and has_lp_core:
        lp_score += 2
    if (has_masuk or has_daftar) and has_lp_core and not has_amp_tech:
        lp_score += 2
    if has_lp_core and game_cards <= 2:
        lp_score += 3  # Landing pages do not have repeated game cards
    if has_lp_core and nav_cat_count <= 1:
        lp_score += 2  # Landing pages lack extensive gaming navigation tabs

    # Penalties for LP if Money Site game grid is present
    if game_cards >= 5 or nav_cat_count >= 3 or linked_image_tiles >= 8 or category_controls >= 4:
        lp_score -= 10

    # Calculate AMP score
    amp_score = 0
    if has_amp_tech:
        amp_score += 12
    if elem_count < 250:
        amp_score += 3
    elif elem_count < 450:
        amp_score += 1
    if visible_imgs <= 3:
        amp_score += 3
    elif visible_imgs <= 5:
        amp_score += 1
    if nav_links <= 3:
        amp_score += 3
    if len(amp_ctas) >= 1 and len(signals.get("buttonTexts", [])) <= 6:
        amp_score += 4
    if text_len >= 300 and p_count >= 1:
        amp_score += 3
    if game_cards >= 3 or cat_count >= 3 or prov_count >= 3:
        amp_score -= 12

    # ================= CLASSIFICATION DECISION LOGIC =================

    # Framework-independent portal fingerprint
    has_catalog_structure = bool(
        game_cards >= 5
        or linked_image_tiles >= 8
        or category_controls >= 4
        or provider_controls >= 4
    )
    has_portal_support = bool(
        nav_cat_count >= 2
        or cat_count >= 3
        or prov_count >= 3
        or has_game_search
        or has_jackpot
        or has_wd_ticker
        or has_play_demo
        or has_rtp
    )
    large_portal_structure = bool(
        internal_links >= 40
        and total_links >= 50
        and cat_count >= 3
        and prov_count >= 3
        and (visible_imgs >= 8 or elem_count >= 600)
    )
    dense_gaming_portal = bool(
        internal_links >= 25
        and cat_count >= 4
        and prov_count >= 5
        and (has_rtp or has_rotating_banner or hero_changed)
    )
    is_genuine_money_site = bool(
        large_portal_structure
        or dense_gaming_portal
        or (has_catalog_structure and has_portal_support)
        or (linked_image_tiles >= 6 and category_controls >= 2 and has_rotating_banner
            and (has_masuk or has_daftar))
        or (nav_cat_count >= 3 and (game_cards >= 2 or has_rtp or has_game_search))
        or (nav_cat_count >= 2 and (has_jackpot or has_wd_ticker or has_game_search)
            and (game_cards >= 1 or linked_image_tiles >= 4))
    )

    # 1. AMP Detection
    # Case A: Technical AMP declaration with minimal UI and no game catalog
    if has_amp_tech and game_cards < 5 and nav_cat_count < 3 and not is_genuine_money_site:
        reason = (
            f"Technical AMP declared ({'amp attr' if signals.get('hasAmpAttr') else 'amp styles/scripts'}), "
            f"minimal UI ({elem_count} elements, {visible_imgs} images), limited CTAs ({len(amp_ctas)} detected), "
            f"SEO content ({text_len} chars)"
        )
        return PAGE_TYPE_AMP, reason, signals

    # Case B: Non-technical lightweight AMP layout
    if (
        amp_score >= 12
        and money_score < 6
        and lp_score < amp_score
        and text_len >= 300
        and p_count >= 1
        and len(amp_ctas) >= 1
        and game_cards < 3
        and nav_cat_count < 2
    ):
        reason = (
            f"Lightweight SEO/content-focused layout ({elem_count} elements, {visible_imgs} images), "
            f"limited CTA links ({len(amp_ctas)} detected), long SEO text ({text_len} chars)"
        )
        return PAGE_TYPE_AMP, reason, signals

    # 2. Money Site Detection. Evaluate strong portal structure before Landing
    # Page traits because gaming portals also contain promotional banners/copy.
    if is_genuine_money_site and money_score >= 8 and not (has_amp_tech and visible_imgs <= 3):
        reason = (
            f"Full-featured portal with {nav_cat_count} navigation categories ({cat_count} total), "
            f"{prov_count} providers, {game_cards} named cards, {linked_image_tiles} linked image tiles, "
            f"{internal_links} internal links, and rotating/changing banner "
            f"({has_rotating_banner or hero_changed})"
        )
        return PAGE_TYPE_MONEY_SITE, reason, signals

    # 3. Landing Page vs Money Site Disambiguation
    # If the page has strong promotional conversion characteristics and lacks a repeated game grid:
    # It is a LANDING_PAGE, even if it mentions game names, providers, or has external links.
    is_promotional_lp = (
        (has_lp_core or bool(amp_html_href) or has_hero or has_promo or has_faq or has_trust or has_reviews)
        and (has_faq or has_trust or external_ctas >= 1 or bool(amp_html_href) or has_reviews or has_masuk or has_daftar or has_promo)
        and game_cards <= 4
        and category_controls < 4
        and nav_cat_count <= 2
        and not hero_changed
        and not large_portal_structure
        and not is_genuine_money_site
    )

    if is_promotional_lp and not has_amp_tech:
        reason = (
            f"Promotional/conversion page with hero banner ({has_hero}), promotional content ({has_promo}), "
            f"FAQ/trust/review badges ({has_faq or has_trust or has_reviews}), "
            f"external funnel CTAs ({external_ctas}), and absence of game card catalog ({game_cards} cards)"
        )
        return PAGE_TYPE_LANDING_PAGE, reason, signals

    # 4. Secondary Landing Page Check (Standard LP threshold)
    if (lp_score >= 8 and (has_lp_core or bool(amp_html_href) or has_hero or has_promo) and not has_amp_tech
            and not hero_changed and not large_portal_structure and not is_genuine_money_site):
        reason = (
            f"Promotional/conversion page with hero banner ({has_hero}), promotional content ({has_promo}), "
            f"FAQ/trust badges ({has_faq or has_trust}), and simple navigation"
        )
        return PAGE_TYPE_LANDING_PAGE, reason, signals

    # 5. Fallback: NORMAL_PAGE (Default safely when signals do not decisively match)
    return (
        PAGE_TYPE_NORMAL_PAGE,
        "Page does not sufficiently match AMP, Landing Page, or Money Site structural fingerprints",
        signals,
    )


def detect_website_type(page: "Page") -> tuple[str, str, dict]:
    """Execute in-browser DOM feature extraction and classify page type."""
    try:
        signals = page.evaluate(PAGE_TYPE_DOM_JS)
        if not signals:
            return PAGE_TYPE_NORMAL_PAGE, "Could not evaluate DOM", {}
        # A runtime-changing hero is a strong negative signal for this project's
        # Landing Page layout. Only observe ambiguous HTTP pages without known
        # carousel markup, avoiding delay when structural evidence is conclusive.
        if (signals.get("hasHeroBanner") and not signals.get("hasRotatingBanner")
                and page.url.startswith(("http://", "https://"))):
            snapshots = []
            for observation in range(4):
                snapshots.append(page.evaluate("""() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > innerWidth * .3 && r.height > 80 && r.top < innerHeight * .8 &&
                            r.bottom > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const nodes = [...document.querySelectorAll(
                        'img, picture source, [class*="hero" i], [class*="banner" i], ' +
                        '[class*="slide" i]'
                    )].filter(visible).slice(0, 20);
                    return nodes.map(el => {
                        const s = getComputedStyle(el);
                        return [el.currentSrc || el.src || el.getAttribute('srcset') || '',
                            s.backgroundImage, s.transform, el.className,
                            el.getAttribute('aria-hidden') || ''].join('|');
                    }).join('||');
                }"""))
                if observation < 3:
                    page.wait_for_timeout(2_000)
            stable_snapshots = [value for value in snapshots if value]
            signals["heroObservedSeconds"] = 6
            signals["heroChanged"] = len(set(stable_snapshots)) > 1
            if signals["heroChanged"]:
                signals["hasRotatingBanner"] = True
        return classify_page_type(signals)
    except Exception as exc:
        return PAGE_TYPE_NORMAL_PAGE, f"DOM evaluation error: {exc}", {}


def normalize_text(text: str) -> str:
    """Strip extra spaces and normalize text."""
    return re.sub(r"\s+", " ", text or "").strip()


def is_amp_cta(label: str) -> bool:
    """Check if label matches limited AMP CTA/access actions."""
    norm = normalize_text(label).casefold()
    if not norm:
        return False
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", norm) for term in AMP_CTA_KEYWORDS)


def is_money_site_label(label: str) -> bool:
    """Check if label matches Money Site restricted auth actions (Masuk, Login, Daftar, Register)."""
    norm = normalize_text(label).casefold()
    if not norm:
        return False
    return any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", norm) for term in MONEY_SITE_LABELS)


def is_logo_control(control: dict, logo_links: set[str], page_url: str) -> bool:
    """Identify if a clickable control is the brand/website logo hyperlink."""
    label = normalize_text(control.get("label", "")).casefold()
    target = control.get("target", "").strip()
    if "logo" in label:
        return True
    if target:
        target = urljoin(page_url, target)
        if target in logo_links:
            return True
        try:
            pt = urlparse(target)
            pp = urlparse(page_url)
            if pt.hostname and pp.hostname and pt.hostname.lower() == pp.hostname.lower():
                if pt.path in ("", "/", "/home", "/desktop/home") and ("beranda" in label or "home" in label or not label):
                    return True
        except Exception:
            pass
    return False


# ==============================================================================
# BRAND DETECTION UPGRADE (Normalization, Homoglyphs & Conservative Fuzzy Matching)
# ==============================================================================
import difflib
import unicodedata

# Common Cyrillic & Greek homoglyphs mapped to ASCII Latin
HOMOGLYPH_MAP = {
    'а': 'a', 'в': 'b', 'с': 'c', 'е': 'e', 'о': 'o', 'р': 'p', 'х': 'x', 'у': 'y',
    'і': 'i', 'ј': 'j', 'ѕ': 's', 'ԁ': 'd', 'ԛ': 'q', 'ԝ': 'w',
    'Α': 'A', 'Β': 'B', 'Ε': 'E', 'Ζ': 'Z', 'Η': 'H', 'Ι': 'I', 'Κ': 'K', 'Μ': 'M',
    'Ν': 'N', 'Ο': 'O', 'Ρ': 'P', 'Τ': 'T', 'Υ': 'Y', 'Χ': 'X',
    'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'О': 'O', 'Р': 'P', 'Х': 'X', 'У': 'Y',
}

# Number-to-letter lookalikes for leetspeak brand variants
LEET_HOMOGLYPH_MAP = {
    '0': 'o',
    '1': 'i',
    '3': 'e',
    '4': 'a',
    '5': 's',
    '7': 't',
}


def normalize_brand_text(text: str, apply_homoglyphs: bool = True, apply_leet: bool = False) -> str:
    """Normalize text by decomposing Unicode, stripping accents, punctuation, and substituting homoglyphs."""
    if not text:
        return ""
    # 1. Unicode decomposition (NFKD) and strip non-spacing marks (accents)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))

    # 2. Casefold
    folded = stripped.casefold()

    # 3. Homoglyph substitution
    if apply_homoglyphs:
        folded = "".join(HOMOGLYPH_MAP.get(c, c) for c in folded)

    # 4. Leetspeak substitution if requested
    if apply_leet:
        folded = "".join(LEET_HOMOGLYPH_MAP.get(c, c) for c in folded)

    # 5. Clean whitespace and non-alphanumeric (keep spaces for word separation)
    cleaned = re.sub(r"[^\w\s]", " ", folded)
    return re.sub(r"\s+", " ", cleaned).strip()


def fuzzy_similarity(a: str, b: str) -> float:
    """Compute normalized SequenceMatcher similarity between two strings."""
    if a == b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def detect_brands_in_text(
    text: str,
    brands: list[str],
    fuzzy_threshold: float = 0.88,
) -> tuple[Optional[str], dict[str, int], dict[str, dict[str, Any]]]:
    """Inspect text for configured brands across exact, normalized, homoglyph, and fuzzy tiers.

    Returns:
    --------
    tuple:
      - dominant_brand: Brand with highest confidence / occurrences or None
      - brand_counts: Dict of brand -> total occurrences
      - brand_details: Dict of brand -> forensic metadata (method, similarity, occurrences)

    IMPORTANT: Fuzzy matching is strictly a detection signal and must NEVER unilaterally
    decide an authentic 'OUR SITE' verdict.
    """
    if not text or not brands:
        return None, {}, {}

    brand_map = {b.casefold(): b for b in brands}
    brand_counts: dict[str, int] = {}
    brand_details: dict[str, dict[str, Any]] = {}

    # Tier 1: Exact / Case-insensitive match on raw text
    for b_norm, original_brand in brand_map.items():
        pattern = rf"(?<![a-zA-Z0-9]){re.escape(b_norm)}(?![a-zA-Z0-9])"
        matches = len(re.findall(pattern, text.casefold()))
        if matches > 0:
            brand_counts[original_brand] = matches
            brand_details[original_brand] = {
                "matched_brand": original_brand,
                "match_method": "exact",
                "similarity": 1.0,
                "occurrences": matches,
            }

    # Tier 2: Normalized (accents, Unicode variants, homoglyphs)
    norm_text = normalize_brand_text(text, apply_homoglyphs=True, apply_leet=False)
    for b_norm, original_brand in brand_map.items():
        norm_b = normalize_brand_text(b_norm, apply_homoglyphs=True, apply_leet=False)
        pattern = rf"(?<![a-zA-Z0-9]){re.escape(norm_b)}(?![a-zA-Z0-9])"
        matches = len(re.findall(pattern, norm_text))
        if matches > 0:
            current = brand_counts.get(original_brand, 0)
            if original_brand not in brand_details:
                brand_counts[original_brand] = matches
                brand_details[original_brand] = {
                    "matched_brand": original_brand,
                    "match_method": "homoglyph",
                    "similarity": 1.0,
                    "occurrences": matches,
                }
            elif matches > current:
                brand_counts[original_brand] = matches

    # Tier 3: Conservative Fuzzy Matching on candidate tokens
    # Only evaluated if no brand detected, or to capture suspicious typosquatting/clones
    words = re.findall(r"\b[a-zA-Z0-9_-]{4,20}\b", norm_text)
    unique_words = set(words)

    for word in unique_words:
        for b_norm, original_brand in brand_map.items():
            norm_b = normalize_brand_text(b_norm)
            if len(word) < max(4, len(norm_b) - 2) or len(word) > len(norm_b) + 2:
                continue
            sim = fuzzy_similarity(word, norm_b)
            if sim >= fuzzy_threshold and sim < 1.0:
                if original_brand not in brand_details:
                    brand_counts[original_brand] = brand_counts.get(original_brand, 0) + 1
                    brand_details[original_brand] = {
                        "matched_brand": original_brand,
                        "match_method": "fuzzy",
                        "similarity": sim,
                        "occurrences": 1,
                        "observed_variant": word,
                    }

    if not brand_counts:
        return None, {}, {}

    # Rank: Prefer exact/homoglyph over fuzzy; then by highest occurrences
    def brand_rank(b):
        detail = brand_details.get(b, {})
        method_weight = 2 if detail.get("match_method") in ("exact", "homoglyph") else 1
        return (method_weight, brand_counts.get(b, 0))

    dominant = max(brand_counts, key=brand_rank)
    return dominant, brand_counts, brand_details
