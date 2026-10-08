#!/usr/bin/env python3
"""Discover, classify, navigate, and export website destinations."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import re
import sys
import time
from typing import Optional
from urllib.parse import unquote, unquote_to_bytes, urljoin, urlparse, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Error, Locator, Page, TimeoutError, sync_playwright

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from rebrandly_matcher import RebrandlyMatcher, ANSI_BOLD_GREEN
from livechat_verifier import (
    LiveChatInfo,
    LiveChatResult,
    extract_live_chat_info,
    verify_live_chat_page,
    format_livechat_output,
)
from content_intelligence import analyze_page_content
from logo_checker import check_image, check_screenshot, get_cache_stats

CHROME_STEALTH_ARGS = [
    # Real Chrome + this flag only (aligned with stealth-template design).
    # Avoid navigator fakes; they create detectable inconsistencies.
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",              # needed on some Windows/CI setups
    "--disable-infobars",
    "--disable-dev-shm-usage",
]

# Strip Playwright main-world markers (same idea as JS stripPlaywrightArtifacts).
# Does NOT touch navigator.webdriver / plugins / languages / canvas.
STRIP_PLAYWRIGHT_ARTIFACTS_JS = """
() => {
  const hide = (k) => {
    try { delete window[k]; } catch (e) {}
    if (Object.prototype.hasOwnProperty.call(window, k)) {
      try {
        Object.defineProperty(window, k, { get: () => undefined, configurable: true });
      } catch (e) {}
    }
  };
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/^__pw|pwInitScripts|playwright/i.test(k)) hide(k);
  }
  if (!window.chrome) window.chrome = {};
}
"""


def human_delay(page=None, min_ms: float = 100, max_ms: float = 500) -> None:
    """Random human-like pause between actions."""
    delay = random.uniform(min_ms, max_ms)
    if page is not None:
        try:
            if not page.is_closed():
                page.wait_for_timeout(int(delay))
                return
        except Exception:
            pass
    time.sleep(delay / 1000.0)


def _page_open(page: Page) -> bool:
    try:
        return page is not None and not page.is_closed()
    except Exception:
        return False


def _viewport(page: Page) -> tuple[int, int]:
    try:
        vp = page.viewport_size or {}
        return int(vp.get("width", 1280)), int(vp.get("height", 800))
    except Exception:
        return 1280, 800


def _bezier_points(x0, y0, x1, y1, steps: int) -> list[tuple[float, float]]:
    """Quadratic Bezier path with a random control point (natural cursor arc)."""
    cx = (x0 + x1) / 2 + random.uniform(-120, 120)
    cy = (y0 + y1) / 2 + random.uniform(-80, 80)
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        # ease-in-out
        te = t * t * (3 - 2 * t)
        x = (1 - te) ** 2 * x0 + 2 * (1 - te) * te * cx + te ** 2 * x1
        y = (1 - te) ** 2 * y0 + 2 * (1 - te) * te * cy + te ** 2 * y1
        pts.append((x, y))
    return pts


# Track last cursor position per page id for continuous movement
_last_mouse: dict[int, tuple[float, float]] = {}


def _last_pos(page: Page) -> tuple[float, float]:
    key = id(page)
    if key not in _last_mouse:
        w, h = _viewport(page)
        _last_mouse[key] = (w * 0.5, h * 0.35)
    return _last_mouse[key]


def _set_pos(page: Page, x: float, y: float) -> None:
    _last_mouse[id(page)] = (x, y)


def human_mouse_to(page: Page, x: float, y: float, steps: int | None = None) -> None:
    """Move cursor along a curved path to (x, y)."""
    if not _page_open(page):
        return
    x0, y0 = _last_pos(page)
    w, h = _viewport(page)
    x = max(2, min(w - 2, x))
    y = max(2, min(h - 2, y))
    dist = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
    n = steps if steps is not None else max(8, min(28, int(dist / 25) + random.randint(5, 12)))
    try:
        for px, py in _bezier_points(x0, y0, x, y, n):
            page.mouse.move(px, py)
            # micro-jitter timing
            if random.random() < 0.15:
                human_delay(page, 8, 25)
        _set_pos(page, x, y)
    except Exception:
        try:
            page.mouse.move(x, y, steps=n)
            _set_pos(page, x, y)
        except Exception:
            pass


def simulate_mouse_movement(page: Page, moves: int | None = None) -> None:
    """
    Multi-stop curved mouse wandering (behavioral only).
    No fingerprint fakes — real Chrome environment only.
    """
    if not _page_open(page):
        return
    count = moves if moves is not None else 4 + random.randint(0, 6)
    w, h = _viewport(page)
    try:
        for _ in range(count):
            tx = 60 + random.random() * max(80, w - 120)
            ty = 60 + random.random() * max(80, h - 120)
            human_mouse_to(page, tx, ty)
            human_delay(page, 50, 220)
            # occasional tiny corrective twitch
            if random.random() < 0.25:
                human_mouse_to(
                    page,
                    tx + random.uniform(-12, 12),
                    ty + random.uniform(-10, 10),
                    steps=random.randint(3, 7),
                )
                human_delay(page, 30, 90)
    except Exception:
        pass


def human_scroll(page: Page, total_delta: int | None = None) -> None:
    """Scroll in several small steps with pauses (reading pattern)."""
    if not _page_open(page):
        return
    if total_delta is None:
        # mostly scroll down a bit; sometimes up
        total_delta = random.randint(180, 720) * (1 if random.random() > 0.15 else -1)
    remaining = total_delta
    try:
        while abs(remaining) > 20:
            step = int(remaining * random.uniform(0.15, 0.35))
            if step == 0:
                step = 40 if remaining > 0 else -40
            page.mouse.wheel(0, step)
            remaining -= step
            human_delay(page, 80, 280)
            if random.random() < 0.3:
                # glance: small mouse move while scrolling
                x, y = _last_pos(page)
                human_mouse_to(page, x + random.uniform(-40, 40), y + random.uniform(-30, 30), steps=6)
    except Exception:
        pass


def human_idle(page: Page, intensity: str = "normal") -> None:
    """
    Combined idle: pause + mouse wander + optional scroll.
    intensity: 'light' | 'normal' | 'heavy'
    """
    if not _page_open(page):
        return
    if intensity == "light":
        human_delay(page, 120, 400)
        simulate_mouse_movement(page, moves=random.randint(2, 4))
    elif intensity == "heavy":
        human_delay(page, 400, 1100)
        simulate_mouse_movement(page, moves=random.randint(5, 9))
        if random.random() < 0.7:
            human_scroll(page)
        human_delay(page, 200, 600)
    else:
        human_delay(page, 200, 700)
        simulate_mouse_movement(page, moves=random.randint(3, 6))
        if random.random() < 0.45:
            human_scroll(page, total_delta=random.randint(120, 480))
        human_delay(page, 100, 350)


def human_click(locator: Locator, page: Page, timeout: float = 5000) -> None:
    """Curved move to control, hover pause, then click with slight offset."""
    try:
        box = locator.bounding_box(timeout=min(timeout, 3500))
        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
            # Prefer center-ish but not exact center (humans rarely hit exact middle)
            tx = box["x"] + box["width"] * random.uniform(0.28, 0.72)
            ty = box["y"] + box["height"] * random.uniform(0.28, 0.72)
            # Approach in two stages: near target, then fine adjust
            human_mouse_to(
                page,
                tx + random.uniform(-25, 25),
                ty + random.uniform(-18, 18),
            )
            human_delay(page, 40, 140)
            human_mouse_to(page, tx, ty, steps=random.randint(4, 9))
            human_delay(page, 70, 260)  # hover before click
            page.mouse.click(tx, ty, delay=random.randint(40, 120))
            _set_pos(page, tx, ty)
            return
        locator.click(timeout=timeout, delay=random.randint(40, 100))
    except Exception:
        try:
            locator.click(timeout=timeout)
        except Exception:
            pass


def challenge_present(page) -> bool:
    try:
        if page.is_closed():
            return False
        return page.evaluate("""() => {
            const title = (document.title || '').toLowerCase();
            const text = (document.body?.innerText || '').toLowerCase();
            const isChallenge = title.includes('just a moment') ||
                ['verifying you are human', 'performing security verification',
                 'checking your browser', 'verify you are human'].some(s => text.includes(s)) ||
                !!document.querySelector('#challenge-running, #challenge-stage, #challenge-form');
            if (isChallenge) return true;

            const hasTurnstile = !!document.querySelector('.cf-turnstile, iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], input[name="cf-turnstile-response"]');
            if (hasTurnstile) {
                const token = document.querySelector("input[name='cf-turnstile-response']")?.value || '';
                if (!token || token.length < 30) {
                    return true;
                }
            }
            return false;
        }""")
    except Exception:
        # A document being replaced or closed is not yet ready for export.
        return not page.is_closed()


def solve_turnstile(page: Page, timeout: float = 12.0) -> bool:
    """Attempt automatic Turnstile verification resolution based on methods analyzed in turnstile_click.py:
    1. Click container (.cf-turnstile)
    2. Switch into Cloudflare iframe and click checkbox / .cb-i / label / body
    3. Coordinate click on the widget/iframe
    4. Wait for cf-turnstile-response token or challenge clearance
    """
    try:
        if page.is_closed():
            return False
        if not challenge_present(page):
            return True

        # Human activity before interacting with challenge widget
        human_idle(page, intensity="normal")

        # Method 1: Click .cf-turnstile container
        try:
            widget = page.locator(".cf-turnstile").first
            if widget.count() > 0 and widget.is_visible():
                print("[Cloudflare] [Method 1] Clicking .cf-turnstile container...", file=sys.stderr, flush=True)
                human_click(widget, page, timeout=2000)
                human_delay(page, 600, 1400)
        except Exception:
            pass

        if page.is_closed() or not challenge_present(page):
            return True

        # Method 2: Iframe + Checkbox
        iframe_selectors = [
            "iframe[src*='challenges.cloudflare.com']",
            "iframe[src*='turnstile']",
            "iframe[title*='Cloudflare']",
            "iframe[title*='Widget containing']",
        ]
        checkbox_selectors = [
            "input[type='checkbox']",
            ".cb-i",
            ".ctp-checkbox-label",
            "label",
            "#challenge-stage input[type='checkbox']",
            "body",
        ]

        clicked = False
        for frame in list(page.frames):
            if page.is_closed():
                return False
            f_url = (frame.url or "").lower()
            if "challenges.cloudflare.com" in f_url or "turnstile" in f_url:
                for sel in checkbox_selectors:
                    try:
                        el = frame.locator(sel).first
                        if el.count() > 0 and el.is_visible():
                            print(f"[Cloudflare] [Method 2] Clicking checkbox inside iframe ({sel})...", file=sys.stderr, flush=True)
                            el.click(timeout=2000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    page.wait_for_timeout(1000)
                    break

        if not clicked and not page.is_closed():
            for ifr_sel in iframe_selectors:
                try:
                    floc = page.frame_locator(ifr_sel)
                    for sel in checkbox_selectors:
                        el = floc.locator(sel).first
                        if el.count() > 0:
                            print(f"[Cloudflare] [Method 2] Clicking {sel} via frame locator...", file=sys.stderr, flush=True)
                            el.click(timeout=2000)
                            clicked = True
                            page.wait_for_timeout(1000)
                            break
                    if clicked:
                        break
                except Exception:
                    continue

        if page.is_closed() or not challenge_present(page):
            return True

        # Method 3: Coordinate click
        try:
            targets = [
                "iframe[src*='challenges.cloudflare.com']",
                "iframe[src*='turnstile']",
                ".cf-turnstile",
                "[data-sitekey]",
                "#challenge-stage",
            ]
            for sel in targets:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    box = loc.bounding_box()
                    if box and box["width"] > 0 and box["height"] > 0:
                        offset_x = box["x"] + min(28, box["width"] / 2)
                        offset_y = box["y"] + box["height"] / 2
                        print(f"[Cloudflare] [Method 3] Coordinate click at ({int(offset_x)}, {int(offset_y)}) on {sel}...", file=sys.stderr, flush=True)
                        human_mouse_to(page, offset_x, offset_y)
                        human_delay(page, 80, 200)
                        page.mouse.click(offset_x, offset_y, delay=random.randint(40, 110))
                        human_delay(page, 700, 1400)
                        break
        except Exception:
            pass

        # Method 4: Check for token or challenge clearance
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline and not page.is_closed():
            if not challenge_present(page):
                return True
            try:
                token = page.evaluate("() => document.querySelector(\"input[name='cf-turnstile-response']\")?.value || ''")
                if token and len(token) > 30:
                    print(f"[Cloudflare] Turnstile token received: {token[:40]}...", file=sys.stderr, flush=True)
                    return True
            except Exception:
                pass
            page.wait_for_timeout(500)

        return not challenge_present(page) if not page.is_closed() else False
    except Exception as exc:
        if "closed" not in str(exc).lower():
            print(f"[Cloudflare] Turnstile resolution notice: {exc}", file=sys.stderr, flush=True)
        return False


def launch_chrome_context(
    playwright,
    headless: bool = False,
    user_data_dir: str | Path | None = "chrome-session",
    channel: str = "chrome",
    accept_downloads: bool = False,
    ignore_https_errors: bool = True,
) -> BrowserContext:
    """Launch real local Chrome browser with a persistent user profile and stealth options."""
    profile_path = Path(user_data_dir or "chrome-session").resolve()
    profile_path.mkdir(parents=True, exist_ok=True)

    try:
        context = playwright.chromium.launch_persistent_context(
            str(profile_path),
            channel=channel,
            headless=headless,
            ignore_https_errors=ignore_https_errors,
            accept_downloads=accept_downloads,
            ignore_default_args=["--enable-automation"],
            args=CHROME_STEALTH_ARGS,
            viewport={"width": 1280, "height": 800},
        )
        try:
            context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS_JS)
        except Exception as _stealth_exc:
            print(f"[Browser] Stealth init script failed: {_stealth_exc}", file=sys.stderr, flush=True)
        return context
    except Exception as exc:
        err = str(exc)
        if "ProcessSingleton" in err or "in use by another instance" in err:
            fallback_dir = profile_path.parent / f"{profile_path.name}_temp_{int(time.time())}"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            print(f"[Browser] Profile directory {profile_path} is locked. Using fallback profile: {fallback_dir}",
                  file=sys.stderr, flush=True)
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(fallback_dir),
                    channel=channel,
                    headless=headless,
                    ignore_https_errors=ignore_https_errors,
                    accept_downloads=accept_downloads,
                    ignore_default_args=["--enable-automation"],
                    args=CHROME_STEALTH_ARGS,
                    viewport={"width": 1280, "height": 800},
                )
                try:
                    context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS_JS)
                except Exception:
                    pass
                return context
            except Exception:
                pass

        if channel == "chrome":
            print(f"[Browser] Real Chrome launch failed ({exc}). Falling back to Chromium.", file=sys.stderr, flush=True)
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_path),
                    headless=headless,
                    ignore_https_errors=ignore_https_errors,
                    accept_downloads=accept_downloads,
                    ignore_default_args=["--enable-automation"],
                    args=CHROME_STEALTH_ARGS,
                    viewport={"width": 1280, "height": 800},
                )
                try:
                    context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS_JS)
                except Exception:
                    pass
                return context
            except Exception:
                browser = playwright.chromium.launch(headless=headless, args=CHROME_STEALTH_ARGS)
                context = browser.new_context(
                    ignore_https_errors=ignore_https_errors,
                    accept_downloads=accept_downloads,
                    viewport={"width": 1280, "height": 800},
                )
                try:
                    context.add_init_script(STRIP_PLAYWRIGHT_ARTIFACTS_JS)
                except Exception:
                    pass
                return context

        raise


def setup_network_interception(context: BrowserContext, config: Optional[dict] = None) -> dict[str, int]:
    """Block video and tracking resources to accelerate crawling while strictly preserving images, CSS, fonts, and scripts."""
    stats = {"blocked_requests": 0, "blocked_video": 0, "blocked_tracking": 0}
    cfg = {
        "block_video": True,
        "block_large_media": True,
        "block_tracking": True,
        "block_fonts": False,
        "block_javascript": False,
    }
    if config:
        cfg.update(config)

    BLOCKED_EXTENSIONS = (".mp4", ".webm", ".avi", ".mkv", ".flv", ".mov", ".m4v")
    TRACKING_DOMAINS = (
        "google-analytics.com", "googletagmanager.com", "connect.facebook.net",
        "doubleclick.net", "adservice.google", "hotjar.com", "clarity.ms",
    )

    def route_handler(route):
        try:
            req = route.request
            r_type = req.resource_type
            url_clean = req.url.split("?")[0].lower()

            # Critical visual, layout, and script assets are NEVER blocked
            if r_type in ("image", "stylesheet", "script", "font", "document", "websocket"):
                if cfg.get("block_tracking") and any(d in url_clean for d in TRACKING_DOMAINS):
                    stats["blocked_requests"] += 1
                    stats["blocked_tracking"] += 1
                    return route.abort()
                return route.continue_()

            if cfg.get("block_video") and (r_type == "media" or url_clean.endswith(BLOCKED_EXTENSIONS)):
                stats["blocked_requests"] += 1
                stats["blocked_video"] += 1
                return route.abort()

            return route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    try:
        context.route("**/*", route_handler)
    except Exception:
        pass
    return stats


def check_cloaking_divergence(page: Page, expected_brand: Optional[str] = None) -> dict[str, Any]:
    """Inspect and compare Desktop (1440x900) vs Mobile (390x844) viewport rendering."""
    try:
        if page.is_closed():
            return {"detected": False, "reason": "Page closed", "desktop_signals": [], "mobile_signals": [], "cta_diff": []}

        # Desktop snapshot (1440x900)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(400)
        desktop_data = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a.btn, a.button, [role="button"], a[href]'))
                .map(e => (e.innerText || e.value || '').trim())
                .filter(t => t.length > 0 && t.length < 30);
            return {
                title: document.title || '',
                text: (document.body?.innerText || '').slice(0, 1000),
                ctas: Array.from(new Set(btns)).slice(0, 20),
            };
        }""")

        # Mobile snapshot (390x844)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        mobile_data = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a.btn, a.button, [role="button"], a[href]'))
                .map(e => (e.innerText || e.value || '').trim())
                .filter(t => t.length > 0 && t.length < 30);
            return {
                title: document.title || '',
                text: (document.body?.innerText || '').slice(0, 1000),
                ctas: Array.from(new Set(btns)).slice(0, 20),
            };
        }""")

        # Reset viewport back to desktop default
        page.set_viewport_size({"width": 1440, "height": 900})

        d_ctas = set(c.lower() for c in desktop_data.get("ctas", []))
        m_ctas = set(c.lower() for c in mobile_data.get("ctas", []))

        # Check for key conversion actions
        key_actions = {"daftar", "login", "masuk", "register", "live chat"}
        d_key = d_ctas & key_actions
        m_key = m_ctas & key_actions

        divergence = False
        reason = "Viewport consistency confirmed"
        cta_diff = []

        if bool(d_key) != bool(m_key):
            divergence = True
            reason = f"Key conversion actions present only in {'desktop' if d_key else 'mobile'} viewport"
            cta_diff = list(d_key ^ m_key)
        elif len(m_ctas) > 0 and len(d_ctas) == 0:
            divergence = True
            reason = "Interactive navigation elements rendered exclusively on mobile viewport"
        elif expected_brand:
            b_norm = expected_brand.lower()
            d_has = b_norm in desktop_data.get("text", "").lower()
            m_has = b_norm in mobile_data.get("text", "").lower()
            if d_has != m_has:
                divergence = True
                reason = f"Brand '{expected_brand}' mentions restricted to {'desktop' if d_has else 'mobile'}"

        return {
            "detected": divergence,
            "reason": reason,
            "desktop_signals": desktop_data.get("ctas", [])[:10],
            "mobile_signals": mobile_data.get("ctas", [])[:10],
            "cta_diff": cta_diff,
        }
    except Exception as exc:
        return {
            "detected": False,
            "reason": f"Cloaking evaluation error: {exc}",
            "desktop_signals": [],
            "mobile_signals": [],
            "cta_diff": [],
        }


def log_crawl_event(event: str, **kwargs) -> None:
    """Output structured key-value log line for observability."""
    timestamp = datetime.now().astimezone().isoformat()
    fields = [f"timestamp={timestamp}", f"event={event}"]
    for k, v in kwargs.items():
        if k not in ("timestamp", "event"):
            fields.append(f"{k}={v}")
    print(f"[Trace] {' '.join(fields)}", flush=True)


LOGO_CANDIDATES_JS = r"""brand => {
    const candidates = [];
    let base = location.href;
    try {
        if (!base || base.startsWith('about:')) {
            base = document.baseURI || document.querySelector('base')?.href || 'https://example.com/';
        }
    } catch {}
    let home = '/';
    try { home = new URL('/', base).href; } catch {}
    const brandName = (brand || '').trim().toLowerCase();

    for (const element of document.querySelectorAll('*')) {
        const tag = element.tagName.toLowerCase();
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;

        let width = rect.width || element.naturalWidth || parseInt(element.getAttribute('width')) || 0;
        let height = rect.height || element.naturalHeight || parseInt(element.getAttribute('height')) || 0;
        if ((width === 0 || height === 0) && element.parentElement) {
            const pRect = element.parentElement.getBoundingClientRect();
            if (width === 0) width = pRect.width;
            if (height === 0) height = pRect.height;
        }

        // Only discard tiny icons if we have confirmed positive dimensions
        if ((width > 0 && width < 20) || (height > 0 && height < 10)) continue;

        let kind;
        let source;
        if (tag === 'img') {
            kind = 'image';
            source = element.currentSrc || element.getAttribute('src') ||
                element.getAttribute('data-src') || element.getAttribute('data-lazy-src') ||
                element.getAttribute('data-original') ||
                element.getAttribute('srcset')?.split(',')[0]?.trim().split(/\s+/)[0];
        } else if (tag === 'svg' && !element.closest('svg svg')) {
            kind = 'svg';
            source = new XMLSerializer().serializeToString(element);
        } else {
            const match = style.backgroundImage.match(/^url\(["']?(.*?)["']?\)$/);
            if (!match) continue;
            kind = 'background';
            try { source = new URL(match[1], base).href; } catch { continue; }
        }
        if (!source) continue;

        const wrapper = element.closest('header, [role="banner"], nav, [class*="header" i], [id*="header" i], [class*="navbar" i], [id*="navbar" i], [class*="logo" i], [id*="logo" i]');
        const link = element.closest('a[href]');
        const parent = element.parentElement;
        const description = [element.id, element.className?.baseVal || element.className,
            element.getAttribute('alt'), element.getAttribute('aria-label'),
            element.getAttribute('title'), link?.getAttribute('aria-label'),
            parent?.id, parent?.className?.baseVal || parent?.className,
            kind === 'svg' ? '' : source].filter(Boolean).join(' ').toLowerCase();

        let anchor = null;
        try { anchor = link ? new URL(link.href, base) : null; } catch {}
        const isHomePath = anchor && (/^\/(?:$|home\b|index(?:\.html?)?\b|[a-z]{2}\/?$)/i.test(anchor.pathname));
        const homeLink = anchor && (anchor.href === home || (anchor.origin === location.origin && isHomePath));

        let score = 0;
        const signals = [];
        if (/\blogo\b|[_-]logo|logo[_-]/.test(description)) { score += 60; signals.push('logo description'); }
        if (/\bbrand\b|[_-]brand|brand[_-]/.test(description)) { score += 30; signals.push('brand description'); }
        if (brandName && description.includes(brandName)) {
            if (!/banner|promo|advert|bonus|slider|carousel/i.test(description)) {
                score += 35;
                signals.push('brand match');
            }
        }
        if (kind === 'image') { score += 8; signals.push('image'); }
        if (wrapper) { score += 20; signals.push('header/nav wrapper'); }
        if (homeLink) { score += 22; signals.push('home link'); }
        if (rect.top >= 0 && rect.top < 300) { score += 12; signals.push('top of page'); }
        if (width >= 40 && width <= 500 && height >= 15 && height <= 200) { score += 15; signals.push('logo dimensions'); }
        if (link && /t\.me|telegram|wa\.me|whatsapp|facebook|twitter|instagram|youtube|tiktok|discord|skype|viber|wechat|line/i.test(link.href)) {
            score -= 100;
            signals.push('social link');
        }
        if (/favicon|avatar|social|sosmed|telegram|whatsapp|wechat|line[-_ ]?chat|facebook|twitter|instagram|youtube|tiktok|discord|sprite|banner|advert|promo|bonus|register|signup|sign-up|login|menu|game|provider|payment|bank|install|shortcut|app[-_ ]?icon|apk|download/i.test(description)) {
            score -= 75;
            signals.push('negative keyword');
        }
        // Animated GIFs are supported for brand logos, so NO penalty for .gif
        if (width > 700 || height > 300) { score -= 50; signals.push('oversized banner'); }
        if (width > 0 && height > 0 && width * height > 200000) { score -= 30; signals.push('large area'); }
        if (rect.bottom < 0 || rect.top > innerHeight) { score -= 20; signals.push('off-screen'); }

        let fullUrl = null;
        if (kind !== 'svg') {
            try { fullUrl = new URL(source, base).href; } catch {}
        }
        if (score >= 15) {
            candidates.push({ kind, url: fullUrl, svg: kind === 'svg' ? source : null, source: `evaluated ${kind}`, score, signals });
        }
    }

    for (const meta of document.querySelectorAll('meta[itemprop="logo"], meta[property="og:logo"]')) {
        if (meta.content) {
            try {
                candidates.push({
                    kind: 'image',
                    url: new URL(meta.content, base).href,
                    svg: null,
                    source: 'metadata logo',
                    score: 80,
                    signals: ['metadata logo']
                });
            } catch {}
        }
    }

    const walk = value => {
        if (!value || typeof value !== 'object') return;
        if (value.logo) {
            const logo = value.logo;
            const logoUrl = typeof logo === 'string' ? logo : logo.contentUrl || logo.url;
            if (logoUrl) {
                try {
                    candidates.push({
                        kind: 'image',
                        url: new URL(logoUrl, base).href,
                        svg: null,
                        source: 'structured logo',
                        score: 85,
                        signals: ['structured logo']
                    });
                } catch {}
            }
        }
        for (const child of Object.values(value)) {
            if (Array.isArray(child)) child.forEach(walk);
            else if (child && typeof child === 'object') walk(child);
        }
    };
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
        try { walk(JSON.parse(script.textContent)); } catch {}
    }

    return candidates.sort((a, b) => b.score - a.score).slice(0, 20);
}"""

FAVICON_CANDIDATES_JS = r"""() => {
    let base = location.href;
    try {
        if (!base || base.startsWith('about:')) {
            base = document.baseURI || document.querySelector('base')?.href || 'https://example.com/';
        }
    } catch {}
    const list = [...document.querySelectorAll('link[rel][href]')]
        .map(link => {
            const rel = (link.rel || '').toLowerCase();
            const relParts = rel.split(/\s+/);
            if (!relParts.includes('icon') && !rel.includes('apple-touch-icon') && rel !== 'mask-icon') return null;
            let priority = relParts.includes('icon') ? 30 : (rel.includes('apple-touch-icon') ? 10 : 5);
            const type = (link.type || '').toLowerCase();
            const href = (link.href || '').toLowerCase();
            if (type === 'image/svg+xml' || href.includes('.svg')) priority += 8;
            const match = (link.sizes?.value || link.getAttribute('sizes') || '').match(/(\d+)x(\d+)/i);
            if (match) priority += Math.min(Number(match[1]), Number(match[2]), 256) / 32;
            let fullUrl = null;
            try { fullUrl = new URL(link.href, base).href; } catch {}
            if (!fullUrl) return null;
            return { url: fullUrl, source: 'declared favicon', score: Math.round(priority) };
        })
        .filter(Boolean)
        .sort((a, b) => b.score - a.score);
    try {
        const fallback = new URL('/favicon.ico', base).href;
        list.push({ url: fallback, source: 'favicon.ico fallback', score: 1 });
    } catch {}
    const seen = new Set();
    return list.filter(item => {
        if (seen.has(item.url)) return false;
        seen.add(item.url);
        return true;
    });
}"""

EXTENSIONS = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/svg+xml': '.svg',
              'image/webp': '.webp', 'image/gif': '.gif', 'image/x-icon': '.ico',
              'image/vnd.microsoft.icon': '.ico', 'image/avif': '.avif'}
MAX_ASSET_BYTES = 10 * 1024 * 1024


def extension_for(content: bytes, content_type: str = '') -> str:
    mime = content_type.split(';')[0].strip().lower()
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if content.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if content[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if content.startswith(b'RIFF') and len(content) >= 12 and content[8:12] == b'WEBP':
        return '.webp'
    if content.startswith(b'\x00\x00\x01\x00'):
        return '.ico'
    prefix = content[:1000].lstrip()
    if (prefix.startswith(b'<svg') or prefix.startswith(b'<?xml')) and b'<svg' in prefix:
        return '.svg'
    if mime == 'image/avif':
        return '.avif'

    mime_map = {
        'image/png': '.png',
        'image/jpeg': '.jpg',
        'image/jpg': '.jpg',
        'image/gif': '.gif',
        'image/webp': '.webp',
        'image/x-icon': '.ico',
        'image/vnd.microsoft.icon': '.ico',
        'image/svg+xml': '.svg',
        'image/avif': '.avif',
    }
    return mime_map.get(mime, '.png')


def image_bytes(page, candidate):
    url = candidate.get('url')
    if candidate.get('svg'):
        return candidate['svg'].encode('utf-8'), 'image/svg+xml'
    if url and url.startswith('data:'):
        header, payload = url.split(',', 1)
        mime = header[5:].split(';')[0].lower()
        return (base64.b64decode(payload) if ';base64' in header else unquote_to_bytes(payload)), mime
    if url and urlparse(url).scheme in {'http', 'https'}:
        response = page.context.request.get(url, headers={'Referer': page.url}, timeout=15000)
        try:
            if not response.ok:
                raise ValueError(f'HTTP {response.status}')
            body = response.body()
            if len(body) > MAX_ASSET_BYTES:
                raise ValueError('Asset exceeds 10 MB')
            return body, response.headers.get('content-type', '').split(';')[0].lower()
        finally:
            response.dispose()
    raise ValueError('Unsupported image URL')


def as_png(page, body, mime):
    if mime not in EXTENSIONS or not body:
        raise ValueError(f'Not a supported image: {mime}')
    converter = page.context.new_page()
    try:
        data = 'data:' + mime + ';base64,' + base64.b64encode(body).decode('ascii')
        encoded = converter.evaluate("""data => new Promise((resolve, reject) => {
            const img = new Image();
            const timer = setTimeout(() => reject(new Error('Image decode timed out')), 5000);
            img.onerror = () => { clearTimeout(timer); reject(new Error('Image decode failed')); };
            img.onload = () => {
                clearTimeout(timer);
                try {
                    const scale = Math.min(1, 4096 / Math.max(img.naturalWidth, img.naturalHeight));
                    const canvas = document.createElement('canvas');
                    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
                    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
                    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
                    resolve(canvas.toDataURL('image/png').split(',')[1]);
                } catch (error) { reject(error); }
            };
            img.src = data;
        })""", data)
        return base64.b64decode(encoded)
    finally:
        converter.close()


def save_image(page, folder, candidates, kind, metadata):
    seen = set()
    for candidate in candidates:
        key = candidate.get('url') or candidate.get('svg')
        if not key or key in seen:
            continue
        seen.add(key)
        print(f'[Export] Trying {kind} candidate {len(seen)}...', flush=True)
        try:
            body, mime = image_bytes(page, candidate)
            ext = extension_for(body, mime)
            filename = f"{kind}{ext}"
            for old_file in folder.glob(f"{kind}.*"):
                if old_file.name != filename and old_file.suffix != '.txt':
                    try:
                        old_file.unlink()
                    except Exception:
                        pass
            (folder / filename).write_bytes(body)
            metadata.update({
                f'{kind}_url': candidate.get('url'),
                f'{kind}_file': filename,
                f'{kind}_source': candidate.get('source'),
                f'{kind}_original_mime': mime,
                f'{kind}_score': candidate.get('score'),
                f'{kind}_signals': candidate.get('signals', []),
            })
            if candidate.get('url'):
                (folder / f'{kind}_url.txt').write_text(candidate['url'] + '\n', encoding='utf-8')
            return
        except (Error, ValueError, Exception) as exc:
            metadata[f'{kind}_errors'].append({'url': candidate.get('url'), 'error': str(exc)})


def save_final_page(page, output_dir, reason, brand=None, page_type=None, page_type_reason=None, page_type_signals=None):
    host = re.sub(r'[^a-zA-Z0-9.-]', '_', urlparse(page.url).hostname or 'page')
    folder = Path(output_dir) / f"{host}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    folder.mkdir(parents=True, exist_ok=False)
    metadata = {'final_url': page.url, 'stop_reason': reason,
                'captured_at': datetime.now().astimezone().isoformat(),
                'page_type': page_type, 'page_type_reason': page_type_reason,
                'page_type_signals': page_type_signals or {},
                'content_file': None, 'content_format': 'rendered HTML',
                'logo_url': None, 'logo_file': None, 'logo_source': None, 'logo_errors': [],
                'favicon_url': None, 'favicon_file': None, 'favicon_source': None, 'favicon_errors': []}
    if challenge_present(page):
        metadata.update(status='blocked', final_url=None, last_reached_url=page.url)
    else:
        (folder / 'content.txt').write_text(page.content(), encoding='utf-8')
        metadata.update(status='exported', title=page.title(), content_file='content.txt')
        save_image(page, folder, page.evaluate(LOGO_CANDIDATES_JS, brand), 'logo', metadata)
        favicons = page.evaluate(FAVICON_CANDIDATES_JS)
        save_image(page, folder, favicons, 'favicon', metadata)
    (folder / 'metadata.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'[Export] Saved: {folder.resolve()}', flush=True)
    for kind in ('logo', 'favicon'):
        print(f'[Export] {kind}: {metadata[kind + "_file"] or "not available (see metadata)"}', flush=True)
    return folder



MAX_CLICKS = 10
NAVIGATION_TIMEOUT_MS = 15_000
SETTLE_TIMEOUT_MS = 2_500

# Earlier entries have higher priority. Indonesian actions are deliberately first.
ACTION_LABELS = [
    "Daftar",
    "Login",
    "Masuk",
    "Daftar Sekarang",
    "Login Sekarang",
    "Buat Akun",
    "Buat Akun Sekarang",
    "Register",
    "Sign In",
    "Join",
    "Continue",
    "Create Account",
    "Sign Up",
    "Log In",
    "Mulai",
    "Lanjut",
    "Main",
    "Main Sekarang",
    "Play",
    "Play Now",
    "Link Alternatif",
    "Alternatif",
    "Klaim Bonus",
    "Klaim",
    "Deposit",
    "Depo",
    "Live Chat",
    "Hubungi CS",
    "Gabung",
    "Gaming",
]

from classification_rules import (
    PAGE_TYPE_LANDING_PAGE,
    PAGE_TYPE_AMP,
    PAGE_TYPE_MONEY_SITE,
    PAGE_TYPE_NORMAL_PAGE,
    PAGE_TYPE_DOM_JS,
    classify_page_type,
    detect_website_type,
    is_amp_cta,
    is_money_site_label,
    is_logo_control,
)

PAGE_TYPES = {
    PAGE_TYPE_LANDING_PAGE,
    PAGE_TYPE_AMP,
    PAGE_TYPE_MONEY_SITE,
    PAGE_TYPE_NORMAL_PAGE,
}

MONEY_CONFIRMATION_LABELS = ("masuk", "daftar", "login", "register", "live chat")
MONEY_CONFIRMATIONS_REQUIRED = 2

PAGE_TYPE_COLORS = {
    PAGE_TYPE_LANDING_PAGE: "\033[93m",  # yellow
    PAGE_TYPE_AMP: "\033[96m",           # cyan
    PAGE_TYPE_MONEY_SITE: "\033[95m",    # magenta
    PAGE_TYPE_NORMAL_PAGE: "\033[90m",   # gray
}
ANSI_RESET = "\033[0m"


def page_type_label(page_type: str, stream=sys.stdout) -> str:
    """Return a distinct colored page-type label for interactive terminals."""
    label = f"[PageType] {page_type}:"
    if hasattr(stream, "isatty") and stream.isatty():
        return f"{PAGE_TYPE_COLORS.get(page_type, '')}{label}{ANSI_RESET}"
    return label


def money_confirmation_priority(label: str) -> int | None:
    """Return the required Money Site action order, or None for other controls."""
    normalized = normalize_text(label).casefold()
    for priority, term in enumerate(MONEY_CONFIRMATION_LABELS):
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized):
            return priority
    return None


def dismiss_page_overlays(page: Page) -> int:
    """Dismiss visible popup overlays before page inspection and discovery."""
    dismissed = 0
    close_selectors = (
        '[role="dialog"] button[aria-label*="close" i], '
        '[role="dialog"] [class*="close" i], '
        '[class*="modal" i] button[aria-label*="close" i], '
        '[class*="modal" i] [class*="close" i], '
        '[class*="popup" i] button[aria-label*="close" i], '
        '[class*="popup" i] [class*="close" i], '
        '[class*="overlay" i] button[aria-label*="close" i], '
        '[data-dismiss="modal"], [data-bs-dismiss="modal"]'
    )
    for frame in page.frames:
        try:
            controls = frame.locator(close_selectors)
            for index in range(min(controls.count(), 10)):
                control = controls.nth(index)
                if control.is_visible():
                    control.click(timeout=1_000, force=True)
                    dismissed += 1
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        hidden = page.evaluate("""() => {
            let count = 0;
            for (const el of document.querySelectorAll('[role="dialog"], [class*="modal" i], [class*="popup" i], [class*="overlay" i], [id*="popup" i], [id*="overlay" i]')) {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const isOverlay = style.position === 'fixed' || style.position === 'absolute' || el.getAttribute('role') === 'dialog' || /popup|overlay/i.test(`${el.id} ${el.className}`);
                if (isOverlay && rect.width > innerWidth * 0.25 && rect.height > innerHeight * 0.2) {
                    el.style.setProperty('display', 'none', 'important');
                    count++;
                }
            }
            document.documentElement.style.overflow = '';
            document.body.style.overflow = '';
            return count;
        }""")
        dismissed += int(hidden or 0)
    except Exception:
        pass
    if dismissed:
        print(f"[Popup] Dismissed {dismissed} overlay(s) before inspection.", flush=True)
    return dismissed


EXCLUDED_PROBE_DOMAINS = (
    "livechatinc.com", "tawk.to", "crisp.chat", "direct.lc.chat", "lc.chat",
    "t.me", "telegram.me", "telegram.org",
    "whatsapp.com", "wa.me", "api.whatsapp.com",
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "curacao-egaming.com", "pagcor.ph", "bmmtestlabs.com", "gaminglabs.com",
    "google.com", "play.google.com", "apple.com", "apps.apple.com",
    "cloudflare.com", "recaptcha.net", "gstatic.com", "googleapis.com",
)


def extract_registered_domain(host: str) -> str:
    """Extract effective registered root domain handling common ccSLDs."""
    host = (host or "").lower().strip().rstrip(".")
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    known_multi_tlds = {
        "co.id", "net.id", "org.id", "web.id", "sch.id", "ac.id", "go.id",
        "com.ph", "org.uk", "co.uk", "com.br", "com.au", "com.my"
    }
    last_two = f"{parts[-2]}.{parts[-1]}"
    if last_two in known_multi_tlds and len(parts) >= 3:
        return f"{parts[-3]}.{last_two}"
    return f"{parts[-2]}.{parts[-1]}"


def is_same_or_subdomain(url1: str, url2: str) -> bool:
    """Check if two URLs share the same registered root domain."""
    h1 = (urlparse(url1).hostname or "").lower()
    h2 = (urlparse(url2).hostname or "").lower()
    if not h1 or not h2:
        return False
    if h1 == h2:
        return True
    return extract_registered_domain(h1) == extract_registered_domain(h2)


def probe_money_site_authenticity(
    page: Page,
    context: BrowserContext,
    page_type_signals: dict,
) -> tuple[bool, Optional[str], str]:
    """Verify that a candidate Money Site is an authentic terminal ecosystem and not an outbound funnel.

    A true Money Site keeps users within its domain or modal popups.
    A Landing Page disguised as a Money Site has primary CTAs (Daftar/Login/Games)
    that redirect outbound to an external destination domain.
    """
    if page.is_closed():
        return False, None, "Page closed before probe"

    current_url = page.url

    # Step 1: Pre-Click Static Link Audit on primary CTAs & links
    button_details = page_type_signals.get("buttonDetails", [])
    if not button_details:
        try:
            button_details = page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('a[href], button[data-href], [role="button"][data-href]'));
                return els.slice(0, 100).map(el => {
                    const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
                    const rawHref = el.getAttribute('href') || el.getAttribute('data-href') || '';
                    return { text, href: rawHref };
                });
            }""")
        except Exception:
            button_details = []

    auth_keywords = (
        "daftar", "register", "sign up", "signup",
        "masuk", "login", "log in",
        "mainkan", "play now", "main sekarang",
        "link alternatif", "alternatif", "link login", "link daftar",
        "klaim", "claim", "gabung", "join", "rtp",
        "klik di sini", "klik disini", "akses website", "kunjungi", "play"
    )

    for btn in button_details:
        txt = (btn.get("text") or "").strip().lower()
        raw_href = (btn.get("href") or "").strip()
        if not raw_href or raw_href.startswith("#") or raw_href.startswith("javascript:"):
            continue

        resolved_href = urljoin(current_url, raw_href)
        if not resolved_href.startswith("http"):
            continue

        is_auth_intent = any(kw in txt for kw in auth_keywords) or any(
            kw in raw_href.lower() for kw in ("register", "daftar", "login", "masuk", "alternatif")
        )

        if is_auth_intent:
            try:
                target_host = (urlparse(resolved_href).hostname or "").lower()
                if target_host and not is_same_or_subdomain(current_url, resolved_href):
                    if not any(ex in target_host for ex in EXCLUDED_PROBE_DOMAINS):
                        return (
                            False,
                            resolved_href,
                            f"Primary CTA '{txt or 'action'}' statically links outbound to external domain: {resolved_href}",
                        )
            except Exception:
                pass

    # Step 2: Interactive Click Probe on primary CTA or game card
    try:
        probe_selectors = [
            "a:has-text('DAFTAR')", "button:has-text('DAFTAR')", "a[href*='daftar' i]",
            "a:has-text('REGISTER')", "button:has-text('REGISTER')", "a[href*='register' i]",
            "a:has-text('LOGIN')", "button:has-text('LOGIN')", "a[href*='login' i]",
            "a:has-text('MASUK')", "button:has-text('MASUK')", "a[href*='masuk' i]",
            "a:has-text('LINK ALTERNATIF')", "a[href*='alternatif' i]",
            "a:has-text('MAINKAN')", "a:has-text('MAIN SEKARANG')",
            ".game-item a", ".game-card a", ".game-box a",
            "a.btn-primary", "a.btn-register", "a.btn-login",
        ]
        target_el = None
        for sel in probe_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    target_el = loc
                    break
            except Exception:
                continue

        # If specific selectors didn't match, probe any prominent game item or action link
        if not target_el:
            try:
                fallback_loc = page.locator("a[href^='http']:not([href*='#'])").first
                if fallback_loc.count() > 0 and fallback_loc.is_visible():
                    target_el = fallback_loc
            except Exception:
                pass

        if target_el:
            initial_url = page.url
            prior_pages = set(context.pages)
            print("[Action Probe] Probing candidate Money Site action for outbound redirect...", flush=True)
            try:
                human_click(target_el, page, timeout=3000)
            except Exception:
                pass
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

            # Check if a new tab was spawned
            new_pages = [p for p in context.pages if p not in prior_pages and not p.is_closed()]
            destination_url = None
            if new_pages:
                spawned = new_pages[-1]
                # Wait briefly if page is resolving from about:blank
                for _ in range(15):
                    if spawned.is_closed() or (spawned.url and spawned.url != "about:blank"):
                        break
                    try:
                        page.wait_for_timeout(200)
                    except Exception:
                        break
                destination_url = spawned.url if not spawned.is_closed() else None
                try:
                    if not spawned.is_closed():
                        spawned.close()
                except Exception:
                    pass
            elif not page.is_closed() and page.url != initial_url:
                destination_url = page.url
                # Try restoring landing page state so asset export records the current page
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=4000)
                except Exception:
                    pass

            if destination_url and not is_same_or_subdomain(initial_url, destination_url):
                dest_host = (urlparse(destination_url).hostname or "").lower()
                if not any(ex in dest_host for ex in EXCLUDED_PROBE_DOMAINS):
                    return (
                        False,
                        destination_url,
                        f"Action click triggered outbound navigation to external domain: {destination_url}",
                    )
    except Exception as probe_err:
        print(f"[Action Probe] Interactive probe notice: {probe_err}", flush=True)

    return True, None, "Action probe confirmed internal domain ecosystem"


CLICKABLE_SELECTOR = ", ".join(
    [
        "a",
        "button",
        "[role='button']",
        "[role='link']",
        "[onclick]",
        "[tabindex]",
        "input[type='button']",
        "input[type='submit']",
    ]
)

# Never click controls that imply credential, payment, or irreversible submission.
BLOCKED_TERMS = {
    "bayar", "payment", "pay now", "purchase", "checkout", "deposit",
    "kirim", "submit", "confirm", "konfirmasi", "verify", "verifikasi",
    "password", "kata sandi", "otp", "pin",
}


@dataclass
class Candidate:
    locator: Locator
    text: str
    description: str
    score: tuple[int, int, int, int]


@dataclass
class Step:
    number: int
    kind: str
    source_url: str
    destination_url: str
    clicked_text: str = ""
    clicked_element: str = ""


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_start_url(value: str) -> str:
    value = value.strip()
    markdown = re.fullmatch(r"\[[^\]]*\]\((https?://.+)\)", value, flags=re.I)
    if markdown:
        value = markdown.group(1)
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provide an HTTP(S) URL, for example https://example.com")
    return value


def normalized_url(url: str) -> str:
    """Normalize only superficial URL differences, without changing destinations."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return f"{parsed.scheme.lower()}://{host}{port}{path}?{parsed.query}#{parsed.fragment}"


def element_text(locator: Locator) -> str:
    try:
        values = locator.evaluate("""el => [el.innerText, el.getAttribute('aria-label'),
            el.value, el.getAttribute('title'),
            ...Array.from(el.querySelectorAll('img[alt]'), img => img.alt)]""")
        for value in values:
            if value and normalize_text(value):
                return normalize_text(value)
    except Error:
        pass
    return ""


def activate_candidate(candidate: Candidate) -> None:
    try:
        candidate.locator.click(timeout=5_000, no_wait_after=True)
    except TimeoutError as exc:
        # Only retry an action that was blocked before dispatch, never a click
        # that already ran and merely timed out waiting for navigation.
        if "intercepts pointer events" not in str(exc):
            raise
        candidate.locator.evaluate("""el => {
            if (!el.isConnected || el.matches(':disabled, [aria-disabled="true"]'))
                throw new Error('Control is no longer enabled');
            el.click();
        }""")
        print(f"Overlay blocked {candidate.text!r}; activated the control directly.",
              file=sys.stderr, flush=True)


def describe_element(locator: Locator) -> str:
    try:
        data = locator.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                id: el.id || '',
                role: el.getAttribute('role') || '',
                href: el.getAttribute('href') || '',
                type: el.getAttribute('type') || '',
                cls: typeof el.className === 'string' ? el.className : ''
            })"""
        )
    except Exception:
        return "clickable element"
    attrs = []
    for key in ("id", "role", "type", "href"):
        if data.get(key):
            attrs.append(f'{key}="{normalize_text(data[key])[:180]}"')
    classes = normalize_text(data.get("cls", ""))
    if classes:
        attrs.append(f'class="{classes[:100]}"')
    return f"<{data.get('tag', 'element')}{(' ' + ' '.join(attrs)) if attrs else ''}>"


def match_score(text: str, label: str, priority: int) -> Optional[tuple[int, int, int, int]]:
    actual = normalize_text(text).casefold()
    wanted = label.casefold()
    if actual == wanted:
        quality = 0
    elif re.fullmatch(rf"[\W_]*{re.escape(wanted)}[\W_]*", actual):
        quality = 1
    elif re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", actual):
        quality = 2
    else:
        return None
    # Label priority comes first, then match quality for that label.
    language_group = 0 if priority < 5 else 1
    return language_group, priority, quality, len(actual)


def find_best_candidate(page: Page) -> Optional[Candidate]:
    candidates = [candidate for frame in page.frames
                  if (candidate := find_frame_candidate(frame)) is not None]
    return min(candidates, key=lambda item: item.score) if candidates else None


def find_frame_candidate(page) -> Optional[Candidate]:
    elements = page.locator(CLICKABLE_SELECTOR)
    candidates: list[Candidate] = []
    try:
        count = min(elements.count(), 500)
    except Exception:
        return None

    for index in range(count):
        element = elements.nth(index)
        try:
            if not element.is_visible() or not element.is_enabled():
                continue
        except Exception:
            continue

        # Navigation links inside forms are fine; submission controls are not.
        try:
            if element.evaluate("""el => {
                const tag = el.tagName.toLowerCase();
                return !!el.form && ((tag === 'button' && el.type === 'submit') ||
                    (tag === 'input' && ['submit', 'image'].includes(el.type)));
            }"""):
                continue
        except Exception:
            continue

        text = element_text(element)
        lowered = text.casefold()
        if not text or any(term in lowered for term in BLOCKED_TERMS):
            continue

        for priority, label in enumerate(ACTION_LABELS):
            score = match_score(text, label, priority)
            if score is None:
                continue
            candidates.append(
                Candidate(element, text, describe_element(element), (*score[:3], index))
            )

    return min(candidates, key=lambda item: item.score) if candidates else None


def page_signature(page: Page) -> str:
    try:
        title = page.title()
        body = page.locator("body").inner_text(timeout=1_500)[:20_000]
        controls = [frame.locator(CLICKABLE_SELECTOR).evaluate_all(
            "els => els.map(el => [el.textContent, el.getAttribute('href'), el.getAttribute('aria-label')])"
        ) for frame in page.frames]
        return hashlib.sha256(f"{normalized_url(page.url)}\n{title}\n{body}\n{controls}".encode()).hexdigest()
    except Exception:
        return hashlib.sha256(normalized_url(page.url).encode()).hexdigest()


def wait_for_verification(page: Page, seconds: float, headless: bool) -> bool:
    try:
        if page.is_closed():
            return False
        if not challenge_present(page):
            return True
        print("[Cloudflare] Cloudflare verification detected", file=sys.stderr, flush=True)
        print(f"Verification required at {page.url}.", file=sys.stderr, flush=True)

        if seconds > 0:
            print("[Cloudflare] Attempting automatic Turnstile verification resolution...", file=sys.stderr, flush=True)
            if solve_turnstile(page, timeout=min(seconds, 12)):
                settle(page)
                if not challenge_present(page):
                    print(f"Verification cleared: {page.url}", file=sys.stderr, flush=True)
                    return True

        if not headless and not page.is_closed():
            try:
                page.bring_to_front()
            except Exception:
                pass
            print("Complete verification in the browser if prompted. Crawling resumes automatically.",
                  file=sys.stderr, flush=True)
        else:
            print("Waiting for automatic verification; run without --headless if interaction is required.",
                  file=sys.stderr, flush=True)
        deadline = time.monotonic() + seconds
        next_update = time.monotonic() + 15
        while time.monotonic() < deadline and not page.is_closed():
            try:
                page.wait_for_timeout(500)
                if not challenge_present(page):
                    settle(page)
                    if not challenge_present(page):
                        print(f"Verification cleared: {page.url}", file=sys.stderr, flush=True)
                        return True
                if time.monotonic() >= next_update:
                    print("Still waiting for website verification...", file=sys.stderr, flush=True)
                    solve_turnstile(page, timeout=3)
                    next_update = time.monotonic() + 15
            except Exception:
                if page.is_closed():
                    return False
        return not challenge_present(page) if not page.is_closed() else False
    except Exception:
        return False


def settle(page: Page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    except TimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    except TimeoutError:
        pass
    try:
        page.wait_for_timeout(random.randint(350, 950))
    except Exception:
        pass
    human_idle(page, intensity=random.choice(["light", "normal", "normal", "heavy"]))


def choose_active_page(context: BrowserContext, prior_pages: list[Page], current: Page) -> Page:
    new_pages = [page for page in context.pages if page not in prior_pages and not page.is_closed()]
    if new_pages:
        selected = new_pages[-1]
        settle(selected)
        return selected
    return current


def crawl(start_url: str, headless: bool = True, output_dir=None, challenge_timeout=0,
          user_data_dir: str | Path = "chrome-session") -> tuple[list[Step], str, str, str]:
    start_url = parse_start_url(start_url)

    steps: list[Step] = []
    stop_reason = "No relevant clickable action exists."

    with sync_playwright() as playwright:
        context = launch_chrome_context(playwright, headless=headless, user_data_dir=user_data_dir)
        page = context.pages[0] if context.pages else context.new_page()

        navigation_events: list[str] = []

        def track_navigation(frame) -> None:
            if frame == frame.page.main_frame and frame.url.startswith(("http://", "https://")):
                navigation_events.append(frame.url)

        def track_request(request) -> None:
            # HTTP redirect hops do not emit framenavigated events.
            if not request.is_navigation_request():
                return
            try:
                if request.frame != request.frame.page.main_frame:
                    return
            except Error:
                # A popup's first request can precede creation of its frame.
                pass
            if request.url.startswith(("http://", "https://")):
                navigation_events.append(request.url)

        context.on("request", track_request)
        context.on("page", lambda opened: opened.on("framenavigated", track_navigation))
        page.on("framenavigated", track_navigation)

        page.goto(start_url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        settle(page)
        wait_for_verification(page, challenge_timeout, headless)
        dismiss_page_overlays(page)
        page_type, page_type_reason, page_type_signals = detect_website_type(page)
        print(f"{page_type_label(page_type, sys.stderr)} {page_type_reason}", file=sys.stderr, flush=True)
        initial_url = page.url
        previous = start_url
        for observed_url in navigation_events:
            if observed_url.startswith(("http://", "https://")) and normalized_url(previous) != normalized_url(observed_url):
                steps.append(Step(0, "Redirect", previous, observed_url))
                previous = observed_url
        visited_urls = {normalized_url(page.url)}
        visited_states = {page_signature(page)}

        for click_number in range(1, MAX_CLICKS + 1):
            if challenge_present(page):
                print("[Cloudflare] Cloudflare verification detected", file=sys.stderr, flush=True)
                stop_reason = "Blocked by browser verification challenge. Destination not reached; content and logo were not exported."
                break
            candidate = find_best_candidate(page)
            deadline = time.monotonic() + SETTLE_TIMEOUT_MS / 1000
            while candidate is None and time.monotonic() < deadline:
                page.wait_for_timeout(200)
                candidate = find_best_candidate(page)
            if candidate is None:
                if challenge_present(page):
                    print("[Cloudflare] Cloudflare verification detected", file=sys.stderr, flush=True)
                    stop_reason = "A browser verification challenge blocks further navigation; this is the last reached URL."
                break

            source_url = page.url
            source_state = page_signature(page)
            prior_pages = list(context.pages)
            event_start = len(navigation_events)

            try:
                print(f"[{click_number}] Click: {candidate.text} at {source_url}",
                      file=sys.stderr, flush=True)
                activate_candidate(candidate)
            except Exception as exc:
                stop_reason = f"Could not click {candidate.text!r} ({candidate.description}): {exc}"
                break

            page = choose_active_page(context, prior_pages, page)
            settle(page)
            # Popups may be created asynchronously after the click handler returns.
            page = choose_active_page(context, prior_pages, page)
            wait_for_verification(page, challenge_timeout, headless)
            dismiss_page_overlays(page)
            page_type, page_type_reason, page_type_signals = detect_website_type(page)
            print(f"{page_type_label(page_type, sys.stderr)} {page_type_reason}", file=sys.stderr, flush=True)
            destination_url = page.url
            print(f"    -> {destination_url}", file=sys.stderr, flush=True)
            observed = []
            for observed_url in navigation_events[event_start:]:
                if not observed or normalized_url(observed_url) != normalized_url(observed[-1]):
                    observed.append(observed_url)
            click_result_url = observed[0] if observed else destination_url

            steps.append(
                Step(
                    click_number,
                    "Click",
                    source_url,
                    click_result_url,
                    candidate.text,
                    candidate.description,
                )
            )

            # Record subsequent client/server navigations in their observed order.
            previous = click_result_url
            for observed_url in observed[1:]:
                if normalized_url(observed_url) != normalized_url(previous):
                    steps.append(Step(click_number, "Redirect", previous, observed_url))
                    previous = observed_url
            if normalized_url(previous) != normalized_url(destination_url):
                steps.append(Step(click_number, "Redirect", previous, destination_url))

            current_url_key = normalized_url(page.url)
            current_state = page_signature(page)
            if (current_url_key != normalized_url(source_url) and current_url_key in visited_urls) or current_state in visited_states or current_state == source_state:
                stop_reason = "A repeated URL/page state was detected."
                break
            visited_urls.add(current_url_key)
            visited_states.add(current_state)
        else:
            stop_reason = f"Maximum depth of {MAX_CLICKS} clicks reached."

        final_url = page.url
        if challenge_present(page):
            print("[Cloudflare] Cloudflare verification detected", file=sys.stderr, flush=True)
            stop_reason = "Blocked by browser verification challenge. Destination not reached; content and logo were not exported."
        if output_dir is not None:
            save_final_page(page, output_dir, stop_reason, page_type=page_type,
                            page_type_reason=page_type_reason, page_type_signals=page_type_signals)
        context.close()
        return steps, initial_url, final_url, stop_reason



def print_report(start_url: str, steps: list[Step], initial_url: str, final_url: str, reason: str) -> None:
    print("START")
    print(start_url)
    if not any(step.number == 0 for step in steps) and normalized_url(start_url) != normalized_url(initial_url):
        print("\n[0] Redirect")
        print(f"{start_url} -> {initial_url}")

    display_number = 0
    for step in steps:
        display_number += 1
        print(f"\n[{display_number}] {step.kind}" + (f": {step.clicked_text}" if step.clicked_text else ""))
        if step.clicked_element:
            print(f"Element: {step.clicked_element}")
        print(f"{step.source_url} -> {step.destination_url}")

    print("\nLAST REACHED URL (BLOCKED)" if reason.startswith("Blocked") else "\nFINAL URL")
    print(final_url)
    print(f"\nStop reason: {reason}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover website URLs and classify reached pages using brand matching and manual logo verification."
    )
    parser.add_argument("url", help="Starting URL, including http:// or https://")
    parser.add_argument('--brands', default='brand.json', help='JSON file containing brand names')
    parser.add_argument('--max-pages', type=int, default=100, help='Maximum navigation attempts per run; 0 checks until the queue is exhausted')
    parser.add_argument('--legacy', action='store_true', help='Use the original prioritized button-chain crawler')
    parser.add_argument("--output-dir", default="Output", help="Folder for page HTML, logos, favicons and reports (default: Output)")
    parser.add_argument("--user-data-dir", default="chrome-session", help="Path to local Chrome profile directory (default: chrome-session)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--headed",
        action="store_true",
        help="Show Chrome while crawling (useful for debugging).",
    )
    mode.add_argument("--headless", action="store_true", help="Hide the browser; terminal Y/N prompts remain enabled.")
    parser.add_argument("--no-ai", action="store_true",
                        help="Disable automated AI logo verification and use manual terminal Y/N prompts.")
    parser.add_argument("--challenge-timeout", type=float, default=180,
                        help="Seconds to wait for verification (default: 180). Browser is visible by default.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        args.url = parse_start_url(args.url)
        if args.challenge_timeout < 0:
            raise ValueError("--challenge-timeout must be nonnegative")
        if args.max_pages < 0:
            raise ValueError('--max-pages must be nonnegative (0 means unlimited)')
        if not args.legacy:
            chosen_classifier = human_logo if args.no_ai else ai_logo_verifier
            mode_desc = "Manual Terminal (Y/N)" if args.no_ai else "Automated AI Logo & Favicon Forensics"
            print(f"[Verification Engine] Using {mode_desc}.", flush=True)
            report = crawl_all(args.url, load_brands(args.brands), args.output_dir,
                args.headless, args.challenge_timeout, args.max_pages,
                classifier=chosen_classifier,
                user_data_dir=args.user_data_dir)
            return 2 if report['status'] == 'LIMIT REACHED' or any(
                p['classification'] == 'BLOCKED' for p in report['pages']) else 1 if report['errors'] else 0
        steps, initial_url, final_url, reason = crawl(args.url, headless=args.headless,
            output_dir=args.output_dir, challenge_timeout=args.challenge_timeout,
            user_data_dir=args.user_data_dir)
        print_report(args.url, steps, initial_url, final_url, reason)
        return 2 if reason.startswith("Blocked") else 1 if reason.startswith("Could not click") else 0
    except KeyboardInterrupt:
        print("Crawler interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Crawler failed: {exc}", file=sys.stderr)
        return 1


ASSET_EXTENSIONS = set(('js jss mjs css map png jpg jpeg gif svg ico webp avif bmp '
    'woff woff2 ttf otf eot mp3 mp4 wav ogg webm mov avi zip gz taYr rar 7z pdf').split())


def url_key(value):
    p = urlsplit(value)
    port = p.port
    host = (p.hostname or '').lower()
    if ':' in host:
        host = f'[{host}]'
    if port and (p.scheme.lower(), port) not in {('http', 80), ('https', 443)}:
        host += f':{port}'
    # Preserve query ordering, trailing slashes and SPA hash routes.
    fragment = p.fragment if p.fragment.startswith(('/', '!')) else ''
    return urlunsplit((p.scheme.lower(), host, p.path or '/', p.query, fragment))


def eligible_url(value, base, asset_extensions=ASSET_EXTENSIONS):
    value = unescape(value).strip().replace('\\/', '/')
    if not value or (value.startswith('#') and not value.startswith(('#/', '#!'))):
        return None
    try:
        absolute = urljoin(base, value)
        p = urlsplit(absolute)
        if p.scheme.lower() not in {'http', 'https'} or not p.hostname or p.username or p.password:
            return None
        if any(segment.rsplit('.', 1)[-1] in asset_extensions
               for segment in unquote(p.path).lower().split('/') if '.' in segment):
            return None
        return url_key(absolute)
    except ValueError:
        return None


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []
        self.base = None
        self.code = []
        self.resources = []

    def handle_data(self, data):
        self.code.append(data)

    def handle_comment(self, data):
        self.code.append(data)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'base' and self.base is None:
            self.base = attrs.get('href')
        for name, value in attrs.items():
            if not value or tag == 'base' or name in {'action', 'formaction'}:
                continue
            if ((tag in {'img', 'script', 'source', 'video', 'audio', 'track', 'embed'}
                 and name in {'src', 'data-src', 'data-lazy-src', 'poster'}) or
                (tag == 'link' and name == 'href' and
                 set(attrs.get('rel', '').lower().split()) &
                 {'stylesheet', 'icon', 'apple-touch-icon', 'preload', 'prefetch', 'preconnect', 'dns-prefetch'})):
                self.resources.append(value)
                continue
            if name in {'href', 'src', 'data-href', 'data-url', 'data-link'}:
                self.values.append(value)
            else:
                self.code.append(value)
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            match = re.search(r'url\s*=\s*(.+)', attrs.get('content', ''), re.I)
            if match:
                self.values.append(match[1].strip("'\" "))


def discover_counts(html, base, asset_extensions=ASSET_EXTENSIONS):
    parser = Links()
    parser.feed(html)
    base = urljoin(base, parser.base) if parser.base else base
    code = unescape('\n'.join(parser.code)).replace('\\/', '/')
    # Absolute URLs anywhere in HTML, plus quoted paths in inline JS/JSON.
    raw = parser.values + re.findall(r'https?://[^\s<>"\'`\\]+', code)
    raw += re.findall(r'''["']((?:/|\./|\.\./)[^"'\s<>]+)["']''', code)
    resources = {eligible_url(value, base, set()) for value in parser.resources}
    return Counter(url for value in raw
                   if (url := eligible_url(value, base, asset_extensions)) and url not in resources)


def discover_html(html, base, asset_extensions=ASSET_EXTENSIONS):
    return list(discover_counts(html, base, asset_extensions))


def action_priority(label, target_url="", expected_brand=None):
    normalized = re.sub(r'\s+', ' ', label or '').strip().casefold()
    target_lower = (target_url or '').lower()
    base_priority = len(ACTION_LABELS)

    for index, wanted in enumerate(ACTION_LABELS):
        if normalized == wanted.casefold():
            base_priority = index
            break
    if base_priority == len(ACTION_LABELS):
        for index, wanted in enumerate(ACTION_LABELS):
            if re.search(rf'(?<!\w){re.escape(wanted.casefold())}(?!\w)', normalized):
                base_priority = index
                break

    # Forensics boost for URLs targeting protected brand or gaming endpoints
    if expected_brand and expected_brand.lower() in target_lower:
        base_priority -= 4
    gaming_keywords = ('/gaming', '/game', '/games', '/slot', '/casino', '/play', '/rtp',
                       '/login', '/register', '/daftar', '/masuk', 'sign_up', 'signup', 'alternatif')
    if any(kw in target_lower for kw in gaming_keywords):
        base_priority -= 3

    # Penalize non-gaming dummy eCommerce/blog paths
    dummy_keywords = ('/t-shirts', '/t-shirt', '/shop', '/product', '/cart', '/tag-directory',
                      '/newest-designers', '/featured-designers', '/category/', '/author/')
    if any(dk in target_lower for dk in dummy_keywords):
        base_priority += 50

    return base_priority


class VisitQueue:
    def __init__(self):
        self.pending = {}
        self.counts = Counter()
        self.priorities = {}
        self.order = 0

    def add(self, url, action=None, count=0, priority=1000):
        key = (url, action)
        self.counts[key] += count
        self.priorities[key] = min(priority, self.priorities.get(key, priority))
        if key not in self.pending:
            self.pending[key] = self.order
            self.order += 1

    def pop(self):
        key = min(self.pending, key=lambda key: (
            self.priorities[key], -self.counts[key], self.pending[key]))
        del self.pending[key]
        return key

    def clear(self):
        self.pending.clear()
        self.counts.clear()
        self.priorities.clear()
        self.order = 0

    def __len__(self):
        return len(self.pending)


def load_brands(filename):
    data = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
    if isinstance(data, dict):
        data = data.get('brands', data.get('brand_name', data.get('brand')))
    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list) or not data or any(not isinstance(x, str) or not x.strip() for x in data):
        raise ValueError('brand.json must contain {"brands": ["Your Brand"]} with at least one real brand.')
    return [x.strip() for x in data]


def dominant_brand(html, brands):
    """Count non-overlapping mentions using upgraded normalization, homoglyphs, and conservative fuzzy matching."""
    from classification_rules import detect_brands_in_text
    dom, counts, _ = detect_brands_in_text(unescape(html), brands)
    return dom, counts


def dominant_brand_with_details(html, brands):
    """Extract dominant brand along with forensic match details (method, similarity)."""
    from classification_rules import detect_brands_in_text
    return detect_brands_in_text(unescape(html), brands)


class VerificationDecision:
    """Wraps verification decision as a bool while preserving rich forensic metadata."""
    def __init__(self, positive: bool, forensics: Optional[dict] = None):
        self.positive = bool(positive)
        self.forensics = forensics or {}

    def __bool__(self):
        return self.positive

    def get(self, key, default=None):
        return self.forensics.get(key, default)


def ai_logo_verifier(url, folder=None, expected_brand=None, page=None):
    """
    Automated multi-tier AI logo & favicon classifier for end-link-lc.
    Communicates with the SigLIP 2 / DINOv2 Logo Forensic Microservice.
    Evaluates both logo and favicon visual assets for full forensic corroboration.
    Preserves rich multi-modal forensic metrics (SigLIP, DINOv2, OCR, Color Delta-E).
    """
    from logo_checker import check_image, check_screenshot
    from pathlib import Path

    print(f"[AI Verifier] Scanning downloaded visual assets at {url}...", flush=True)
    logo_res = None
    fav_res = None
    last_res = None

    if folder:
        folder_p = Path(folder)
        logo_file = folder_p / "logo.png"
        if not logo_file.exists():
            for f in folder_p.glob("logo.*"):
                if f.suffix not in ('.txt', '.json'):
                    logo_file = f
                    break
        fav_file = folder_p / "favicon.png"
        if not fav_file.exists():
            for f in folder_p.glob("favicon.*"):
                if f.suffix not in ('.txt', '.json'):
                    fav_file = f
                    break

        # Scan logo.png
        if logo_file.exists() and logo_file.stat().st_size > 100:
            try:
                logo_res = check_image(logo_file, filename=logo_file.name, asset_mode="logo")
                last_res = logo_res
                if logo_res.get("is_our_logo"):
                    det_brand = logo_res.get("brand")
                    conf = logo_res.get("confidence", 0.0)
                    print(f"[AI Verifier] Logo MATCH: brand='{det_brand}', confidence={conf:.1%}", flush=True)
                else:
                    print(f"[AI Verifier] Logo check verdict: {logo_res.get('verdict')} (brand={logo_res.get('brand')})", flush=True)
            except Exception as e:
                print(f"[AI Verifier] Logo check warning: {e}", flush=True)

        # Scan favicon.png (always inspect favicon when available, never skip!)
        if fav_file.exists() and fav_file.stat().st_size > 50:
            try:
                fav_res = check_image(fav_file, filename=fav_file.name, asset_mode="favicon")
                last_res = fav_res
                if fav_res.get("is_our_logo"):
                    det_brand = fav_res.get("brand")
                    conf = fav_res.get("confidence", 0.0)
                    print(f"[AI Verifier] Favicon MATCH: brand='{det_brand}', confidence={conf:.1%}", flush=True)
                else:
                    print(f"[AI Verifier] Favicon check verdict: {fav_res.get('verdict')} (brand={fav_res.get('brand')})", flush=True)
            except Exception as e:
                print(f"[AI Verifier] Favicon check warning: {e}", flush=True)

    # Corroborate and combine results from both extracted assets
    logo_match = bool(logo_res and logo_res.get("is_our_logo"))
    fav_match = bool(fav_res and fav_res.get("is_our_logo"))

    # Core Rule: If an extracted logo asset was scanned and its verdict is UNKNOWN or not our logo,
    # that is NOT our site. A screenshot must NOT override an UNKNOWN logo asset.
    if logo_res and not logo_match:
        print(f"[AI Verifier] Extracted logo check verdict is {logo_res.get('verdict')} (not authentic site logo). Disqualifying as NOT OUR SITE.", flush=True)
        fallback_forensics = dict(logo_res)
        fallback_forensics["logo"] = logo_res
        fallback_forensics["favicon"] = fav_res
        fallback_forensics["is_our_logo"] = False
        fallback_forensics["reason"] = f"Extracted logo verdict: {logo_res.get('verdict')}"
        return VerificationDecision(False, fallback_forensics)

    # If no logo asset was found at all (e.g. pure CSS/canvas), only then fall back to screenshot scanner
    if logo_res is None and page is not None and not page.is_closed():
        try:
            print("[AI Verifier] No logo asset found; running full-page screenshot scanner to locate header logo...", flush=True)
            ss_bytes = page.screenshot(type="png", full_page=False)
            res = check_screenshot(ss_bytes, filename="page_capture.png")
            last_res = res
            if res.get("is_our_logo"):
                logo_res = res
                logo_match = True
                det_brand = res.get("brand")
                conf = res.get("confidence", 0.0)
                print(f"[AI Verifier] Page Screenshot Logo MATCH: brand='{det_brand}', confidence={conf:.1%}", flush=True)
        except Exception as e:
            print(f"[AI Verifier] Screenshot scanner warning: {e}", flush=True)

    # Core Forensic Rule: LOGO verification is mandatory to confirm our authentic site!
    # Favicon alone cannot declare a site authentic if the actual logo is UNKNOWN/unverified.
    if logo_match:
        combined_forensics = dict(logo_res)
        combined_forensics["logo"] = logo_res
        combined_forensics["favicon"] = fav_res
        combined_forensics["both_matched"] = bool(logo_match and fav_match)
        if fav_match:
            b_logo = (logo_res.get("brand") or "").lower()
            b_fav = (fav_res.get("brand") or "").lower()
            if b_logo and b_fav and b_logo != b_fav:
                print(f"[AI Verifier] Warning: Brand divergence between logo ('{b_logo}') and favicon ('{b_fav}')", flush=True)

        det_brand = logo_res.get("brand")
        if not expected_brand or (det_brand and det_brand.lower() == expected_brand.lower()):
            return VerificationDecision(True, combined_forensics)
        print(f"[AI Verifier] Note: Expected '{expected_brand}', detected '{det_brand}'.", flush=True)
        return VerificationDecision(True, combined_forensics)

    if fav_match:
        # Favicon matched, but logo could not be verified on the page
        print(f"[AI Verifier] Notice: Favicon matched '{fav_res.get('brand')}', but authentic logo was UNKNOWN/unverified on the page canvas.", flush=True)
        fallback_forensics = dict(fav_res)
        fallback_forensics["is_our_logo"] = False
        fallback_forensics["logo"] = logo_res
        fallback_forensics["favicon"] = fav_res
        fallback_forensics["reason"] = "Favicon matched, but logo unverified"
        return VerificationDecision(False, fallback_forensics)

    print(f"[AI Verifier] No authentic protected brand logos identified for {expected_brand or 'page'}.", flush=True)
    fallback_forensics = last_res or logo_res or fav_res or {"verdict": "UNKNOWN", "reason": "No authentic logo identified"}
    if isinstance(fallback_forensics, dict):
        fallback_forensics["logo"] = logo_res
        fallback_forensics["favicon"] = fav_res
    return VerificationDecision(False, fallback_forensics)


def human_logo(url, **kwargs):
    while True:
        answer = input(f'Logo identified at {url}? [Y/N]: ').strip().upper()
        if answer in {'Y', 'N'}:
            is_pos = (answer == 'Y')
            return VerificationDecision(is_pos, {"verdict": "MATCH" if is_pos else "UNKNOWN", "source": "human_prompt"})
        print('Please enter Y for Yes or N for No.')


CONTROL_SNAPSHOT_JS = """els => ({
    base: document.baseURI,
    controls: els.map((el, index) => {
        const href = el.getAttribute('href') || '';
        const style = getComputedStyle(el);
        return {
            index,
            label: [el.innerText, el.matches('input[type=button], input[type=submit]') ? el.value : '',
                el.getAttribute('aria-label'), el.getAttribute('title'),
                ...Array.from(el.querySelectorAll('img[alt]'), i => i.alt)].filter(Boolean).join(' '),
            target: href || el.getAttribute('data-href') || el.getAttribute('data-url') || el.getAttribute('data-link') || '',
            safe: !el.closest('form') && !el.matches(':disabled, [aria-disabled="true"], input[type=submit], input[type=image]') &&
                !el.hasAttribute('download') && !el.querySelector('input') &&
                !!el.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none' &&
                (!href || /^javascript:/i.test(href) || (href.startsWith('#') && el.hasAttribute('onclick')))
        };
    })
})"""


def snapshot_controls(frame):
    """One browser round trip; no per-element locator auto-waits on a changing DOM."""
    return frame.locator(CLICKABLE_SELECTOR).evaluate_all(CONTROL_SNAPSHOT_JS)


def crawl_all(start_url, brands, output_dir='Output', headless=False,
              challenge_timeout=180, max_pages=100,
              classifier=human_logo, asset_extensions=ASSET_EXTENSIONS,
              user_data_dir: str | Path = "chrome-session"):
    start_url = parse_start_url(start_url)
    from datetime import datetime
    root = Path(output_dir) / datetime.now().strftime('crawl_%Y%m%d_%H%M%S_%f')
    root.mkdir(parents=True)
    report = {'start_url': start_url, 'brands': brands, 'classifier': 'manual',
              'classification': 'INCONCLUSIVE', 'pages': [], 'redirects': [], 'errors': [],
              'rebrandly_matches': [],
              'live_chat_info': None,
              'live_chat_verification': None,
              'max_pages': max_pages, 'stop_on_logo_missing': True}
    rebrandly_matcher = RebrandlyMatcher()
    seen_rebrandly_alerts = set()

    def check_and_notify_rebrandly(url: str, context_str: str = "") -> Optional[dict]:
        if not url:
            return None
        match = rebrandly_matcher.check_url(url)
        if match:
            alert_key = (match.brand, match.source_type, match.sheet_url)
            if alert_key not in seen_rebrandly_alerts:
                seen_rebrandly_alerts.add(alert_key)
                print(rebrandly_matcher.format_match_message(match), flush=True)
            match_data = {
                'brand': match.brand,
                'source_type': match.source_type,
                'sheet_name': match.sheet_name,
                'url': match.matched_url,
                'sheet_url': match.sheet_url,
                'context': context_str,
            }
            if not any(m['url'] == match.matched_url and m['brand'] == match.brand for m in report['rebrandly_matches']):
                report['rebrandly_matches'].append(match_data)
            return match_data
        return None

    queue = VisitQueue()
    queue.add(start_url)
    visited = set()
    attempted = set()
    counted_pages = {}
    opened_urls = set()
    attempts = 0
    yes_streak = no_streak = 0
    crawl_brand = None
    finished = False
    money_site_reached = False
    money_confirmations = 0
    report.update(money_confirmations=0,
                  money_confirmations_required=MONEY_CONFIRMATIONS_REQUIRED)

    def schedule(page, counts):
        print('[Discovery] Identifying URLs and prioritizing navigation actions...', flush=True)
        priorities = {}
        actions = []
        frames = list(page.frames)
        for fi, frame in enumerate(frames):
            try:
                snapshot = snapshot_controls(frame)
            except Error as exc:
                print(f'[Discovery] Frame changed or detached; skipping controls: {str(exc).splitlines()[0]}', flush=True)
                continue
            for control in snapshot['controls']:
                label = control['label']
                target = eligible_url(control['target'], snapshot['base'], asset_extensions)
                priority = action_priority(label, target_url=target or '', expected_brand=crawl_brand)
                if target:
                    priorities[target] = min(priority, priorities.get(target, priority))
                    counts.setdefault(target, 1)
                    continue
                if control['safe'] and not any(term in label.lower() for term in BLOCKED_TERMS):
                    actions.append((fi, control['index'], priority))
        fallback = action_priority('', expected_brand=crawl_brand)
        print(f'[Discovery] {sum(counts.values())} eligible URL occurrences; {len(counts)} unique; '
              f'{sum(counts.values()) - len(counts)} duplicate occurrences removed. '
              'Static assets and non-HTTP URLs excluded.', flush=True)
        skipped = sum(url in visited or (url, None) in attempted for url in counts)
        merged = sum((url, None) in queue.pending for url in counts)
        previous_counts = counted_pages.setdefault(url_key(page.url), Counter())
        for url, count in counts.items():
            url_p = action_priority('', target_url=url, expected_brand=crawl_brand)
            p = min(priorities.get(url, fallback), url_p)
            priorities[url] = p
            if url not in visited and (url, None) not in attempted:
                queue.add(url, count=max(0, count - previous_counts[url]), priority=p)
            previous_counts[url] = max(count, previous_counts[url])
        for fi, ei, priority in actions:
            if (page.url, (fi, ei)) not in attempted:
                queue.add(page.url, (fi, ei), count=1, priority=priority)
        print(f'[Queue] Skipped {skipped} already visited/attempted URLs; merged {merged} already queued URLs. '
              f'{len(queue)} destinations/actions pending.', flush=True)
        return sorted(counts, key=lambda url: (priorities.get(url, fallback), -counts[url]))

    def schedule_landing_page(page, counts, signals):
        print('[Discovery] [LANDING_PAGE] Identifying navigation URLs prioritizing ACTION_LABELS and amphtml...', flush=True)
        priorities = {}
        actions = []
        frames = list(page.frames)
        amp_href = signals.get('ampHtmlHref', '')
        if amp_href:
            target_amp = eligible_url(amp_href, page.url, asset_extensions)
            if target_amp:
                priorities[target_amp] = -1
                counts.setdefault(target_amp, 1)
                print(f'[Discovery] [LANDING_PAGE] Prioritizing declared amphtml destination: {target_amp}', flush=True)

        for fi, frame in enumerate(frames):
            try:
                snapshot = snapshot_controls(frame)
            except Error as exc:
                continue
            for control in snapshot['controls']:
                label = control['label']
                target = eligible_url(control['target'], snapshot['base'], asset_extensions)
                priority = action_priority(label, target_url=target or '', expected_brand=crawl_brand)
                if target:
                    priorities[target] = min(priority, priorities.get(target, priority))
                    counts.setdefault(target, 1)
                    continue
                if control['safe'] and not any(term in label.lower() for term in BLOCKED_TERMS):
                    actions.append((fi, control['index'], priority))

        fallback = action_priority('', expected_brand=crawl_brand)
        for url, count in counts.items():
            url_p = action_priority('', target_url=url, expected_brand=crawl_brand)
            p = min(priorities.get(url, fallback), url_p)
            priorities[url] = p
            if url_key(url) not in visited and (url, None) not in attempted:
                queue.add(url, count=count, priority=p)
        for fi, ei, priority in actions:
            if (page.url, (fi, ei)) not in attempted:
                queue.add(page.url, (fi, ei), count=1, priority=priority)
        print(f'[Queue] [LANDING_PAGE] {len(queue)} destinations/actions pending.', flush=True)
        return sorted(counts, key=lambda url: (priorities.get(url, fallback), -counts[url]))

    def schedule_amp(page, counts):
        print('[Discovery] [AMP] Identifying limited CTA access links (LOGIN, DAFTAR, LINK ALTERNATIF)...', flush=True)
        priorities = {}
        actions = []
        amp_counts = Counter()
        frames = list(page.frames)
        for fi, frame in enumerate(frames):
            try:
                snapshot = snapshot_controls(frame)
            except Error as exc:
                continue
            for control in snapshot['controls']:
                label = control['label']
                if not is_amp_cta(label):
                    continue
                target = eligible_url(control['target'], snapshot['base'], asset_extensions)
                priority = action_priority(label, target_url=target or '', expected_brand=crawl_brand)
                if target:
                    priorities[target] = min(priority, priorities.get(target, priority))
                    amp_counts[target] = max(amp_counts[target], counts.get(target, 1))
                    continue
                if control['safe'] and not any(term in label.lower() for term in BLOCKED_TERMS):
                    actions.append((fi, control['index'], priority))

        fallback = action_priority('', expected_brand=crawl_brand)
        for url, count in amp_counts.items():
            url_p = action_priority('', target_url=url, expected_brand=crawl_brand)
            p = min(priorities.get(url, fallback), url_p)
            priorities[url] = p
            if url_key(url) not in visited and (url, None) not in attempted:
                queue.add(url, count=count, priority=p)
        for fi, ei, priority in actions:
            if (page.url, (fi, ei)) not in attempted:
                queue.add(page.url, (fi, ei), count=1, priority=priority)
        print(f'[Queue] [AMP] {len(queue)} CTA access destinations/actions pending.', flush=True)
        return sorted(amp_counts, key=lambda url: (priorities.get(url, fallback), -amp_counts[url]))

    def schedule_money_site(page, counts, signals=None):
        print(f'[Discovery] [MONEY_SITE] Selecting {MONEY_CONFIRMATIONS_REQUIRED} confirmation clicks: Masuk, Daftar, Login, Register, then Live Chat.', flush=True)
        # Stop broad discovery: clear broad queued links
        queue.clear()

        candidates = []
        signals = signals or {}
        logo_links = set(signals.get('logoLinks', []))
        frames = list(page.frames)

        for fi, frame in enumerate(frames):
            try:
                snapshot = snapshot_controls(frame)
            except Error as exc:
                continue
            for control in snapshot['controls']:
                label = control['label']
                target = eligible_url(control['target'], snapshot['base'], asset_extensions)
                label_priority = money_confirmation_priority(label)
                is_label_match = label_priority is not None
                is_logo_match = is_logo_control(control, logo_links, page.url)

                if not (is_label_match or is_logo_match):
                    continue

                if any(term in label.lower() for term in BLOCKED_TERMS):
                    continue
                # Known navigation anchors are safe to click. Script controls
                # still use the snapshot's conservative safety check.
                if not target and not control['safe']:
                    continue
                priority = label_priority if is_label_match else len(MONEY_CONFIRMATION_LABELS) + 10
                candidates.append((priority, fi, control['index'], label, bool(is_label_match), bool(is_logo_match)))

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        selected = candidates[:MONEY_CONFIRMATIONS_REQUIRED]
        live_chat = next((item for item in candidates if item[0] == 4), None)
        if live_chat and live_chat not in selected:
            if MONEY_CONFIRMATIONS_REQUIRED >= 3 and len(selected) == MONEY_CONFIRMATIONS_REQUIRED:
                selected[-1] = live_chat
            elif len(selected) < MONEY_CONFIRMATIONS_REQUIRED:
                selected.append(live_chat)

        for priority, fi, ei, label, _, _ in selected:
            if (page.url, (fi, ei)) not in attempted:
                queue.add(page.url, (fi, ei), count=1, priority=priority)

        labels = [normalize_text(item[3]) or 'Logo' for item in selected]
        print(f'[Queue] [MONEY_SITE] Queued {len(queue)}/{MONEY_CONFIRMATIONS_REQUIRED} confirmation clicks: '
              f'{", ".join(labels) if labels else "none"}.', flush=True)
        return labels

    with sync_playwright() as pw:
        context = launch_chrome_context(pw, headless=headless, user_data_dir=user_data_dir, accept_downloads=False)
        net_stats = setup_network_interception(context)
        context.set_default_timeout(5000)
        pages_in_context = 0
        domains_in_context = set()
        try:
            while queue and (max_pages == 0 or attempts < max_pages) and not finished:
                requested, action = queue.pop()
                attempted.add((requested, action))
                if action is None and url_key(requested) in visited:
                    print(f'[Skip] Already visited: {requested}', flush=True)
                    continue
                if context.is_closed():
                    print("[Browser] Browser context closed unexpectedly. Re-launching context...", flush=True)
                    try:
                        context = launch_chrome_context(
                            playwright,
                            headless=headless,
                            user_data_dir=user_data_dir,
                            accept_downloads=False,
                            ignore_https_errors=True,
                        )
                        setup_network_interception(context)
                    except Exception as re_err:
                        print(f"[Browser] Re-launch failed: {re_err}", flush=True)
                        break

                try:
                    page = context.pages[0] if (context.pages and not context.pages[0].is_closed()) else context.new_page()
                except Exception:
                    context = launch_chrome_context(
                        playwright,
                        headless=headless,
                        user_data_dir=user_data_dir,
                        accept_downloads=False,
                        ignore_https_errors=True,
                    )
                    setup_network_interception(context)
                    page = context.pages[0] if (context.pages and not context.pages[0].is_closed()) else context.new_page()

                record = {'requested_url': requested}
                t_page_start = time.perf_counter()
                trace_id = hashlib.md5(f"{requested}_{attempts}".encode()).hexdigest()[:8]
                log_crawl_event("navigation_start", trace_id=trace_id, requested_url=requested, attempt=attempts)

                try:
                    requested_key = url_key(requested)
                    if action is None:
                        opened_urls.add(requested_key)
                        known_urls = {key[0] for key in queue.counts} | visited | opened_urls
                        print(f'[Open {len(opened_urls)}/{len(known_urls)}] {requested}', flush=True)
                    else:
                        print(f'[Action] Reopening source page: {requested}', flush=True)

                    t_nav_start = time.perf_counter()
                    response = page.goto(requested, wait_until='domcontentloaded', timeout=NAVIGATION_TIMEOUT_MS)
                    print('[Load] Waiting briefly for redirects and page content...', flush=True)
                    settle(page)
                    if action is not None:
                        action_source = page.url
                        print('[Action] Activating queued navigation control (timeout: 5s)...', flush=True)
                        frame_index, element_index = action
                        prior = list(context.pages)
                        try:
                            dismiss_page_overlays(page)
                            loc = page.frames[frame_index].locator(CLICKABLE_SELECTOR).nth(element_index)
                            human_idle(page, intensity=random.choice(["light", "normal"]))
                            if random.random() < 0.4:
                                human_scroll(page, total_delta=random.randint(80, 320))
                            human_click(loc, page, timeout=5000)
                        except Exception as click_err:
                            print(f"[Action] Control click notice: {click_err}", flush=True)
                        settle(page)
                        page = choose_active_page(context, prior, page)
                        settle(page)
                        if url_key(action_source) != url_key(page.url):
                            print(f'[Redirect] {action_source} -> {page.url}', flush=True)
                            check_and_notify_rebrandly(action_source, 'action_source')
                            check_and_notify_rebrandly(page.url, 'action_destination')
                    nav_ms = int((time.perf_counter() - t_nav_start) * 1000)

                    if response:
                        hops = []
                        req = response.request
                        while req:
                            hops.append(req.url)
                            req = req.redirected_from
                        hops = hops[::-1]
                        report['redirects'].append({'requested_url': requested, 'http_hops': hops, 'reached_url': page.url})
                        displayed_hops = list(hops)
                        if not displayed_hops:
                            displayed_hops.append(requested)
                        if url_key(displayed_hops[-1]) != url_key(page.url):
                            displayed_hops.append(page.url)
                        for source, destination in zip(displayed_hops, displayed_hops[1:]):
                            if url_key(source) != url_key(destination):
                                print(f'[Redirect] {source} -> {destination}', flush=True)
                                check_and_notify_rebrandly(source, 'redirect_hop_source')
                                check_and_notify_rebrandly(destination, 'redirect_hop_destination')
                    check_and_notify_rebrandly(page.url, 'page_url')
                    if not wait_for_verification(page, challenge_timeout, headless):
                        record.update(url=page.url, classification='BLOCKED')
                        record['folder'] = str(save_final_page(page, root, 'Blocked by verification'))
                        report['pages'].append(record)
                        log_crawl_event("page_blocked", trace_id=trace_id, url=page.url)
                        continue

                    # Website type detection
                    dismiss_page_overlays(page)
                    page_type, page_type_reason, page_type_signals = detect_website_type(page)
                    print(f'{page_type_label(page_type)} {page_type_reason}', flush=True)

                    # Targeted Money Site Action Probe: Confirm internal ecosystem vs outbound funnel
                    if page_type == PAGE_TYPE_MONEY_SITE:
                        is_confirmed_money, outbound_target, probe_reason = probe_money_site_authenticity(
                            page, context, page_type_signals
                        )
                        if not is_confirmed_money and outbound_target:
                            print(f"[Action Probe] Demoted candidate Money Site -> LANDING_PAGE: {probe_reason}", flush=True)
                            page_type = PAGE_TYPE_LANDING_PAGE
                            page_type_reason = f"Demoted from Money Site to Landing Page (Outbound Funnel): {probe_reason}"
                            # Enqueue outbound target with top priority to continue crawling toward true destination!
                            queue.add(outbound_target, count=10, priority=-10)
                        else:
                            print(f"[Action Probe] Confirmed authentic Money Site destination ({probe_reason}).", flush=True)

                    clean_signals = {
                        k: v for k, v in page_type_signals.items()
                        if k not in ('buttonDetails',)
                    }
                    record['page_type'] = page_type
                    record['page_type_reason'] = page_type_reason
                    record['page_type_signals'] = clean_signals

                    key = url_key(page.url)
                    htmls = []
                    for frame in list(page.frames):
                        try:
                            if not page.is_closed():
                                htmls.append((frame.content(), frame.url))
                        except Exception:
                            pass
                    if not htmls and not page.is_closed():
                        try:
                            htmls = [(page.content(), page.url)]
                        except Exception:
                            pass
                    counts = Counter()
                    for html, base in htmls:
                        counts.update(discover_counts(html, base, asset_extensions))
                    page_rebrandly_matches = []
                    for discovered_url in counts:
                        m_info = check_and_notify_rebrandly(discovered_url, 'discovered_url')
                        if m_info:
                            page_rebrandly_matches.append(m_info)
                    is_money_confirmation = money_site_reached and action is not None
                    if key in visited and not is_money_confirmation:
                        print('[Skip] Duplicate destination; no repeat logo question.', flush=True)
                        if action is not None and not money_site_reached:
                            if page_type == PAGE_TYPE_LANDING_PAGE:
                                schedule_landing_page(page, counts, page_type_signals)
                            elif page_type == PAGE_TYPE_AMP:
                                schedule_amp(page, counts)
                            else:
                                schedule(page, counts)
                        continue
                    links = list(counts)
                    visited.add(key)
                    record['url'] = page.url
                    record['links'] = links
                    selected_brand, brand_counts = dominant_brand(
                        '\n'.join(h for h, _ in htmls), brands)
                    matches = [selected_brand] if selected_brand else []
                    record['brand_matches'] = matches
                    record['page_dominant_brand'] = selected_brand
                    if crawl_brand is None:
                        crawl_brand = selected_brand
                    record['selected_brand'] = crawl_brand
                    report['selected_brand'] = crawl_brand
                    record['brand_counts'] = brand_counts

                    # Content intelligence extraction (independent pipeline)
                    try:
                        body_txt = page.inner_text("body", timeout=3000)
                    except Exception:
                        body_txt = '\n'.join(h for h, _ in htmls)
                    content_intel = analyze_page_content({
                        "url": page.url,
                        "title": page.title() or "",
                        "text": body_txt,
                        "headings": page.evaluate("() => Array.from(document.querySelectorAll('h1, h2, h3')).map(e => e.innerText || '')") if not page.is_closed() else [],
                        "buttons": list(counts.keys())[:20],
                        "page_type": page_type,
                    })
                    record['content_intelligence'] = content_intel

                    # Asset extraction & verification timing
                    t_asset_start = time.perf_counter()

                    # 1. Starting page brand check: If no brand occurs on starting page, follow links before deciding
                    if crawl_brand is None and page_type != PAGE_TYPE_MONEY_SITE:
                        print('[Export] Saving HTML, logo and favicon...', flush=True)
                        folder = save_final_page(page, root, 'No brand found', crawl_brand,
                                                 page_type, page_type_reason, clean_signals)
                        asset_ms = int((time.perf_counter() - t_asset_start) * 1000)
                        ai_ms = 0
                        record['folder'] = str(folder)
                        links = schedule(page, counts)
                        record['links'] = links
                        if queue and (max_pages == 0 or attempts < max_pages):
                            print(f"[Workflow] No brand detected on initial doorway. Traversal continuing toward destination ({len(queue)} pending actions in queue)...", flush=True)
                            classification = 'AWAITING DESTINATION'
                        else:
                            classification = 'TEMPROVERLY STRAY DOMAIN'
                            report['classification'] = classification
                            finished = True
                            print(f'\nURL: {page.url}')
                            print(classification, flush=True)
                    else:
                        print('[Export] Saving HTML, logo and favicon...', flush=True)
                        folder = save_final_page(page, root, 'Awaiting manual classification', crawl_brand,
                                                 page_type, page_type_reason, clean_signals)
                        asset_ms = int((time.perf_counter() - t_asset_start) * 1000)
                        record['folder'] = str(folder)
                        print(f'\nURL: {page.url}')
                        if len(visited) == 1 and crawl_brand:
                            print(f'BRAND FOUND: {crawl_brand} ({brand_counts.get(crawl_brand, 0)} occurrences)')
                        if crawl_brand:
                            print(f'Check the logo and favicon for {crawl_brand}.')
                        else:
                            print('Scanning logo and favicon against all protected brands...')

                        t_ai_start = time.perf_counter()
                        try:
                            positive = classifier(page.url, folder=folder, expected_brand=crawl_brand, page=page)
                        except TypeError:
                            positive = classifier(page.url)
                        ai_ms = int((time.perf_counter() - t_ai_start) * 1000)

                        is_pos = bool(positive)
                        forensics_data = getattr(positive, 'forensics', None)
                        record['logo_identified'] = is_pos
                        record['logo_forensics'] = forensics_data
                        print(f'[Verification] {"Y confirmed; processing next step" if is_pos else "N confirmed (intermediate or unverified)"}.', flush=True)

                        # Viewport Cloaking Inspection for suspicious pages
                        cloaking_res = {"detected": False, "reason": "Not triggered"}
                        if not is_pos or (selected_brand and selected_brand.lower() != (crawl_brand or "").lower()) or page_type == PAGE_TYPE_MONEY_SITE:
                            cloaking_res = check_cloaking_divergence(page, expected_brand=crawl_brand)
                            record['cloaking'] = cloaking_res
                            if cloaking_res.get("detected"):
                                print(f"[Cloaking] Forensic notice: {cloaking_res.get('reason')}", flush=True)

                        # Preserve detailed forensic evidence for suspicious findings
                        if not is_pos or cloaking_res.get("detected"):
                            forensics_dir = folder / "forensics"
                            try:
                                forensics_dir.mkdir(parents=True, exist_ok=True)
                                forensic_summary = {
                                    "url": page.url,
                                    "expected_brand": crawl_brand,
                                    "dominant_brand": selected_brand,
                                    "logo_forensics": forensics_data,
                                    "cloaking": cloaking_res,
                                    "content_intelligence": content_intel,
                                }
                                (forensics_dir / "forensic_summary.json").write_text(json.dumps(forensic_summary, indent=2), encoding="utf-8")
                            except Exception:
                                pass

                        yes_streak = yes_streak + 1 if is_pos else 0
                        no_streak = 0 if is_pos else no_streak + 1
                        record.update(yes_streak=yes_streak, no_streak=no_streak)
                        report.update(yes_streak=yes_streak, no_streak=no_streak)

                        if page_type == PAGE_TYPE_MONEY_SITE:
                            money_site_reached = True
                            finished = True
                            queue.clear()
                            links = []

                            print(f'[Money Site] Terminal destination reached. Inspecting Logo, Favicon, and Live Chat...', flush=True)
                            print('[LiveChat] Analyzing Live Chat pattern from website script/template...', flush=True)
                            livechat_info = extract_live_chat_info(page, htmls, expected_brand=crawl_brand)
                            if livechat_info:
                                print(f'[LiveChat] Identified Provider: {livechat_info.provider.upper()}', flush=True)
                                print(f'[LiveChat] Identified Live Chat ID: {livechat_info.chat_id}', flush=True)
                                print(f'[LiveChat] Identified Base URL: {livechat_info.base_url}', flush=True)
                                print(f'[LiveChat] Constructed URL: {livechat_info.constructed_url}', flush=True)
                                print('[LiveChat] Opening Live Chat page to verify interface...', flush=True)
                                livechat_result = verify_live_chat_page(context, livechat_info.constructed_url, expected_brand=crawl_brand, live_chat_info=livechat_info)
                            else:
                                livechat_result = LiveChatResult(
                                    is_live_chat=False,
                                    status='NOT FOUND',
                                    details='No Live Chat script or template pattern found on Money Site.'
                                )

                            output_banner = format_livechat_output(livechat_info, livechat_result, colorize=True)
                            print(f'\n{output_banner}\n', flush=True)

                            record['live_chat_info'] = livechat_info.to_dict() if livechat_info else None
                            record['live_chat_verification'] = livechat_result.to_dict()
                            report['live_chat_info'] = livechat_info.to_dict() if livechat_info else None
                            report['live_chat_verification'] = livechat_result.to_dict()

                            # Asset forensics evaluation: Logo, Favicon, Live Chat
                            logo_data = forensics_data.get('logo') if isinstance(forensics_data, dict) else (forensics_data if is_pos else None)
                            fav_data = forensics_data.get('favicon') if isinstance(forensics_data, dict) else None

                            if classifier == human_logo:
                                logo_is_ours = is_pos
                                fav_is_ours = is_pos
                            else:
                                logo_is_ours = bool(logo_data and logo_data.get('is_our_logo')) if logo_data else is_pos
                                fav_is_ours = bool(fav_data and fav_data.get('is_our_logo')) if fav_data else False

                            chat_is_ours = bool(livechat_result and livechat_result.is_live_chat and livechat_result.status == 'VERIFIED')

                            target_brand = crawl_brand or selected_brand or (logo_data.get('brand') if logo_data else None) or (fav_data.get('brand') if fav_data else None)
                            logo_brand = (logo_data.get('brand') or '').lower() if logo_data else ''
                            fav_brand = (fav_data.get('brand') or '').lower() if fav_data else ''

                            brand_conflict = bool(
                                (logo_is_ours and target_brand and logo_brand and logo_brand != target_brand.lower())
                                or (fav_is_ours and target_brand and fav_brand and fav_brand != target_brand.lower())
                                or (logo_is_ours and fav_is_ours and logo_brand and fav_brand and logo_brand != fav_brand)
                            )

                            # STRICT FORENSIC RULE: Logo, Favicon, and Live Chat MUST ALL be OURS.
                            # If ANY one thing fails, the site is NOT OURS (PHISHING).
                            all_ours = bool(logo_is_ours and fav_is_ours and chat_is_ours and not brand_conflict)

                            if all_ours:
                                classification = 'OUR SITE'
                                print(f'[Verification] Confirmed: Logo, Favicon, and Live Chat all belong to OUR SITE ({target_brand.upper()}).', flush=True)
                            else:
                                reasons = []
                                if not logo_is_ours:
                                    reasons.append("LOGO MISMATCH")
                                if not fav_is_ours:
                                    reasons.append("FAVICON MISMATCH")
                                if not chat_is_ours:
                                    c_status = livechat_result.status if livechat_result else "NOT FOUND"
                                    reasons.append(f"LIVE CHAT {c_status}")
                                if brand_conflict:
                                    reasons.append("BRAND CONFLICT")

                                b_label = (target_brand or "UNKNOWN").upper()
                                reason_str = ", ".join(reasons)
                                print(f'[Verification] Forensic verdict: NOT OUR SITE ({reason_str}).', flush=True)
                                classification = f'{b_label} PHISHING ({reason_str})'

                            report['classification'] = classification
                            links = []

                        elif is_money_confirmation:
                            money_confirmations += 1
                            report['money_confirmations'] = money_confirmations
                            record['money_confirmation'] = money_confirmations
                            links = []
                            if money_confirmations >= MONEY_CONFIRMATIONS_REQUIRED:
                                classification = 'OUR SITE'
                                report['classification'] = 'OUR SITE'
                                finished = True

                        else:
                            # Intermediate page (NORMAL_PAGE, LANDING_PAGE, or AMP)
                            if page_type == PAGE_TYPE_LANDING_PAGE:
                                links = schedule_landing_page(page, counts, page_type_signals)
                                print(f'[Workflow] LANDING_PAGE detected; following prioritized navigation URLs toward AMP ({len(links)} links).', flush=True)
                            elif page_type == PAGE_TYPE_AMP:
                                links = schedule_amp(page, counts)
                                print(f'[Workflow] AMP detected; following limited CTA access links toward Money Site ({len(links)} links).', flush=True)
                            else:
                                links = schedule(page, counts)
                                print(f'[Workflow] NORMAL_PAGE detected; following prioritized navigation URLs toward end destination ({len(links)} links).', flush=True)

                            # Do not take premature stopping decision on intermediate pages!
                            if queue and (max_pages == 0 or attempts < max_pages):
                                classification = 'LOGO CONFIRMED' if is_pos else 'INCONCLUSIVE (INTERMEDIATE)'
                                print(f'[Workflow] Intermediate {page_type} inspected (logo confirmed: {is_pos}). Traversal continuing toward end Money Site destination ({len(queue)} pending actions in queue)...', flush=True)
                            else:
                                finished = True
                                classification = 'LOGO CONFIRMED' if is_pos else f'{crawl_brand} PHISHING'
                                report['classification'] = classification
                        if not money_site_reached:
                            print(f'Logo-positive pages: {yes_streak}; no positive-answer stopping limit.', flush=True)

                    total_page_ms = int((time.perf_counter() - t_page_start) * 1000)
                    record['performance'] = {
                        "navigation_ms": nav_ms,
                        "asset_extraction_ms": asset_ms,
                        "ai_verification_ms": ai_ms,
                        "total_ms": total_page_ms,
                    }
                    record['links'] = links
                    record['url_counts'] = dict(counts)
                    record.update(total_urls=len(counts), total_url_occurrences=sum(counts.values()),
                                  duplicate_url_occurrences=sum(counts.values()) - len(counts),
                                  frame_count=len(htmls) - 1)
                    print(f'Unique eligible URLs ({len(links)}), in priority order:')
                    for link in links:
                        m_link = rebrandly_matcher.check_url(link)
                        if m_link:
                            print(f'  {ANSI_BOLD_GREEN}{link} ({counts[link]} occurrences) [{m_link.brand} {m_link.source_type}]{ANSI_RESET}')
                        else:
                            print(f'  {link} ({counts[link]} occurrences)')
                    record['classification'] = classification
                    record['rebrandly_matches'] = page_rebrandly_matches
                    print(classification, flush=True)

                    log_crawl_event(
                        "page_completed",
                        trace_id=trace_id,
                        domain=urlparse(page.url).netloc,
                        classification=classification,
                        page_type=page_type,
                        duration_ms=total_page_ms,
                    )

                    metadata_path = folder / 'metadata.json'
                    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                    metadata.update(classification=classification, classifier='manual',
                                    logo_identified=record.get('logo_identified'), brand_matches=matches,
                                    rebrandly_matches=page_rebrandly_matches,
                                    live_chat_info=record.get('live_chat_info'),
                                    live_chat_verification=record.get('live_chat_verification'),
                                    selected_brand=crawl_brand, page_dominant_brand=selected_brand, brand_counts=brand_counts,
                                    yes_streak=yes_streak, no_streak=no_streak,
                                    money_confirmations=money_confirmations,
                                    money_confirmations_required=MONEY_CONFIRMATIONS_REQUIRED,
                                    configured_brands=brands, used_brand=crawl_brand,
                                    detected_brands=list(brand_counts),
                                    requested_url=requested, frame_count=len(htmls) - 1,
                                    total_urls=len(counts), total_url_occurrences=sum(counts.values()),
                                    duplicate_url_occurrences=sum(counts.values()) - len(counts),
                                    url_counts=dict(counts), urls_in_priority_order=links,
                                    navigation_attempt=attempts,
                                    stop_reason=classification,
                                    content_intelligence=record.get('content_intelligence'),
                                    logo_forensics=record.get('logo_forensics'),
                                    cloaking=record.get('cloaking'),
                                    performance=record.get('performance'))
                    metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                    report['pages'].append(record)
                except (Error, ValueError, IndexError, Exception) as exc:
                    report['errors'].append({'url': requested, 'error': str(exc)})
                    print(f'Could not inspect {requested}: {exc}', flush=True)
                finally:
                    try:
                        if not context.is_closed():
                            next_page = context.new_page()
                            for opened in list(context.pages):
                                if opened != next_page and not opened.is_closed():
                                    try:
                                        opened.close()
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    try:
                        (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                    except Exception as rep_err:
                        print(f"[Report] Warning: Could not write report.json: {rep_err}", file=sys.stderr, flush=True)

                    # Safe BrowserContext lifecycle recycling
                    pages_in_context += 1
                    req_dom = urlparse(requested).netloc
                    if req_dom:
                        domains_in_context.add(req_dom)
                    if (pages_in_context >= 50 or len(domains_in_context) >= 25) and queue:
                        print(f"[Browser] Recycling browser context ({pages_in_context} pages, {len(domains_in_context)} domains)...", flush=True)
                        try:
                            context.close()
                        except Exception:
                            pass
                        context = launch_chrome_context(pw, headless=headless, user_data_dir=user_data_dir, accept_downloads=False)
                        setup_network_interception(context)
                        context.set_default_timeout(5000)
                        pages_in_context = 0
                        domains_in_context.clear()

            report['pending_count'] = len(queue)
            blocked = any(p['classification'] == 'BLOCKED' for p in report['pages'])
            if not finished and not money_site_reached:
                has_our_site = any(p.get('classification') == 'OUR SITE' for p in report['pages'])
                has_pos_logo = any(p.get('logo_identified') for p in report['pages'])
                if has_our_site:
                    report['classification'] = 'OUR SITE'
                elif has_pos_logo:
                    report['classification'] = 'LOGO CONFIRMED'
                elif report['errors'] and not report['pages']:
                    report['classification'] = 'ERROR'
                elif crawl_brand:
                    if any('logo_identified' in p for p in report['pages']):
                        report['classification'] = f'{crawl_brand} PHISHING'
                    else:
                        report['classification'] = 'INCONCLUSIVE'
                else:
                    report['classification'] = 'TEMPROVERLY STRAY DOMAIN'
            report.update(total_unique_urls=len({key[0] for key in queue.counts} | visited),
                          visited_url_count=len(visited), navigation_attempts=attempts,
                          checked_page_count=sum('logo_identified' in p for p in report['pages']),
                          classification_scope='Money Site classification requires 1 logo confirmation and Live Chat verification')
            report['status'] = 'CLASSIFIED' if (finished or not queue) else 'LIMIT REACHED' if queue else 'COMPLETED WITH ERRORS' if report['errors'] else 'COMPLETED'
        finally:
            try:
                (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            except Exception:
                pass

            # Performance telemetry report
            perf_report = {
                "total_navigation_attempts": attempts,
                "total_pages_visited": len(visited),
                "network_interception": net_stats,
                "ai_cache_stats": get_cache_stats(),
                "completed_at": datetime.now().astimezone().isoformat(),
            }
            try:
                (root / "performance_report.json").write_text(json.dumps(perf_report, indent=2), encoding="utf-8")
            except Exception:
                pass

            try:
                context.close()
            except Exception:
                pass
    print(f'\n{report["classification"]} - {report["status"]}. Report: {(root / "report.json").resolve()}')
    return report


if __name__ == "__main__":
    raise SystemExit(main())
