"""Live Chat extraction and verification for Money Sites.

Analyzes website scripts/templates to identify Live Chat IDs and base URLs,
constructs the standardized Live Chat URL ({base_url}/index.html?id=<ID>),
and verifies whether the target is an actual active Live Chat interface or a random/invalid page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Optional, Any
from urllib.parse import urlparse, parse_qs, urlunsplit

# ANSI color codes
ANSI_BOLD_CYAN = "\033[1;96m"
ANSI_BOLD_GREEN = "\033[1;92m"
ANSI_BOLD_RED = "\033[1;91m"
ANSI_BOLD_YELLOW = "\033[1;93m"
ANSI_RESET = "\033[0m"

# Enable ANSI escape sequences on Windows console
if sys.platform == "win32":
    try:
        os.system("")
    except Exception:
        pass


@dataclass
class LiveChatInfo:
    """Extracted Live Chat configuration."""
    base_url: str
    chat_id: str
    constructed_url: str
    language: Optional[str] = None
    source: str = "script"  # 'script', 'iframe', 'link', 'dom', 'template', 'network'
    raw_snippet: Optional[str] = None
    provider: str = "onechat"
    evidence_source: str = "script"
    account_id: Optional[str] = None
    expected_account_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "chat_id": self.chat_id,
            "account_id": self.account_id or self.chat_id,
            "expected_account_id": self.expected_account_id,
            "constructed_url": self.constructed_url,
            "language": self.language,
            "source": self.source,
            "evidence_source": self.evidence_source,
            "raw_snippet": self.raw_snippet[:200] if self.raw_snippet else None,
        }


@dataclass
class LiveChatResult:
    """Result of Live Chat verification."""
    is_live_chat: bool
    status: str  # 'VERIFIED', 'UNKNOWN', 'INVALID ID', 'RANDOM PAGE', 'NOT FOUND', 'ERROR'
    live_chat_info: Optional[LiveChatInfo] = None
    page_title: str = ""
    greeting_text: str = ""
    buttons: list[str] = field(default_factory=list)
    brand_matched: bool = False
    details: str = ""
    provider_evidence: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_live_chat": self.is_live_chat,
            "status": self.status,
            "live_chat_info": self.live_chat_info.to_dict() if self.live_chat_info else None,
            "page_title": self.page_title,
            "greeting_text": self.greeting_text[:300] if self.greeting_text else "",
            "buttons": self.buttons,
            "brand_matched": self.brand_matched,
            "details": self.details,
            "provider_evidence": self.provider_evidence or (self.live_chat_info.to_dict() if self.live_chat_info else None),
        }


def load_brand_livechat_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """Load brand live chat configuration from brand_livechat_config.json.

    Supports both full object mapping and simple string license ID mapping:
    - {"DEWI11": {"license_id": "18263412", "group_id": "4", ...}}
    - {"DEWI11": "18263412"}
    """
    if not config_path:
        base_dir = Path(__file__).resolve().parent
        candidate = base_dir / "brand_livechat_config.json"
        if candidate.exists():
            config_path = str(candidate)
        else:
            return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        normalized = {}
        for k, v in raw.items():
            k_upper = k.strip().upper()
            if isinstance(v, str):
                normalized[k_upper] = {"license_id": v.strip(), "account_id": v.strip()}
            elif isinstance(v, dict):
                norm_entry = dict(v)
                acc = norm_entry.get("license_id") or norm_entry.get("account_id") or norm_entry.get("token")
                if acc:
                    norm_entry["account_id"] = str(acc).strip()
                    norm_entry["license_id"] = str(acc).strip()
                normalized[k_upper] = norm_entry
        return normalized
    except Exception:
        return {}


def _extract_live_chat_info_core(page, htmls: Optional[list[tuple[str, str]]] = None) -> Optional[LiveChatInfo]:
    # 1. Try DOM evaluation first if page is accessible
    if page:
        try:
            dom_data = page.evaluate("""() => {
                // Check iframes
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (const ifr of iframes) {
                    const src = ifr.getAttribute('src') || '';
                    if (src.includes('direct.lc.chat') || src.includes('livechatinc.com')) {
                        return { type: 'iframe', provider: 'livechat', src: src, outer: ifr.outerHTML.slice(0, 300) };
                    }
                    if (src.includes('widget-embed')) {
                        return { type: 'iframe', provider: 'widget-embed', src: src, outer: ifr.outerHTML.slice(0, 300) };
                    }
                    if (src.includes('index.html?id=') || src.includes('/chat') || src.includes('onechat')) {
                        return { type: 'iframe', src: src, outer: ifr.outerHTML.slice(0, 300) };
                    }
                    if (src.includes('tawk.to')) {
                        return { type: 'iframe', provider: 'tawk', src: src, outer: ifr.outerHTML.slice(0, 300) };
                    }
                    if (src.includes('crisp.chat')) {
                        return { type: 'iframe', provider: 'crisp', src: src, outer: ifr.outerHTML.slice(0, 300) };
                    }
                }

                // Check onclick handlers (e.g. openLiveChat('https://direct.lc.chat/...'))
                const clickables = Array.from(document.querySelectorAll('[onclick]'));
                for (const el of clickables) {
                    const onclickAttr = el.getAttribute('onclick') || '';
                    if (onclickAttr.includes('direct.lc.chat') || onclickAttr.includes('livechatinc.com')) {
                        return { type: 'onclick', provider: 'livechat', text: onclickAttr, outer: el.outerHTML.slice(0, 300) };
                    }
                    if (onclickAttr.includes('widget-embed') || onclickAttr.includes('openLiveChat')) {
                        return { type: 'onclick', provider: 'widget-embed', text: onclickAttr, outer: el.outerHTML.slice(0, 300) };
                    }
                    if (onclickAttr.includes('index.html?id=')) {
                        return { type: 'onclick', text: onclickAttr, outer: el.outerHTML.slice(0, 300) };
                    }
                }

                // Check anchor links
                const links = Array.from(document.querySelectorAll('a[href]'));
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (href.includes('direct.lc.chat') || href.includes('livechatinc.com')) {
                        return { type: 'link', provider: 'livechat', src: href, outer: a.outerHTML.slice(0, 300) };
                    }
                    if (href.includes('widget-embed')) {
                        return { type: 'link', provider: 'widget-embed', src: href, outer: a.outerHTML.slice(0, 300) };
                    }
                    if (href.includes('index.html?id=')) {
                        return { type: 'link', src: href, outer: a.outerHTML.slice(0, 300) };
                    }
                    if (href.includes('tawk.to')) {
                        return { type: 'link', provider: 'tawk', src: href, outer: a.outerHTML.slice(0, 300) };
                    }
                }

                // Check script elements
                const scripts = Array.from(document.querySelectorAll('script'));
                for (const s of scripts) {
                    const text = s.innerText || '';
                    const src = s.getAttribute('src') || '';
                    if (src.includes('direct.lc.chat') || text.includes('direct.lc.chat') || src.includes('livechatinc.com') || text.includes('window.__lc')) {
                        return { type: 'script', provider: 'livechat', text: text.slice(0, 1000), src: src };
                    }
                    if (text.includes('widget-embed') || src.includes('widget-embed')) {
                        return { type: 'script', provider: 'widget-embed', text: text.slice(0, 2000), src: src };
                    }
                    if (text.includes('WebSDK') || text.includes('BASE_URL') || text.includes('onechat') || text.includes('index.html?id=')) {
                        return { type: 'script', text: text.slice(0, 2000), src: src };
                    }
                    if (src.includes('embed.tawk.to') || text.includes('Tawk_API')) {
                        return { type: 'script', provider: 'tawk', text: text.slice(0, 1000), src: src };
                    }
                    if (src.includes('crisp.chat') || text.includes('CRISP_WEBSITE_ID')) {
                        return { type: 'script', provider: 'crisp', text: text.slice(0, 1000), src: src };
                    }
                    if (src.includes('zdassets.com') || text.includes('zopim')) {
                        return { type: 'script', provider: 'zendesk', text: text.slice(0, 1000), src: src };
                    }
                }

                return null;
            }""")
            if dom_data:
                provider = dom_data.get("provider")
                src = dom_data.get("src") or ""
                text = dom_data.get("text") or ""
                outer = dom_data.get("outer") or ""

                target_blob = " ".join(filter(None, [src, text, outer]))

                # Provider: LiveChat Inc / direct.lc.chat
                if provider == "livechat" or "direct.lc.chat" in target_blob or "livechatinc.com" in target_blob or "window.__lc" in target_blob:
                    # 1. Check direct.lc.chat/<license_id>/<group_id>
                    lc_m = re.search(r'direct\.lc\.chat/(\d+)(?:/(\d+))?', target_blob, re.I)
                    if lc_m:
                        license_id = lc_m.group(1)
                        group_id = lc_m.group(2)
                        constructed = f"https://direct.lc.chat/{license_id}/{group_id}" if group_id else f"https://direct.lc.chat/{license_id}/"
                        chat_id = f"{license_id}/{group_id}" if group_id else license_id
                        return LiveChatInfo(
                            base_url="https://direct.lc.chat",
                            chat_id=chat_id,
                            constructed_url=constructed,
                            source=dom_data.get("type", "dom"),
                            provider="livechat",
                            evidence_source=dom_data.get("type", "dom"),
                            account_id=license_id,
                            raw_snippet=outer or target_blob[:200]
                        )
                    # 2. Check query parameter or license config: ?license_id=<id>&group=<group> or license = <id>
                    lic_param = re.search(r'(?:[?&]license_id=|\blicense(?:_id)?\s*[:=]\s*["\']?)(\d+)', target_blob, re.I)
                    if lic_param:
                        license_id = lic_param.group(1)
                        grp_m = re.search(r'(?:[?&]group=|\bgroup\s*[:=]\s*["\']?)(\d+)', target_blob, re.I)
                        group_id = grp_m.group(1) if grp_m else None
                        constructed = f"https://direct.lc.chat/{license_id}/{group_id}" if group_id else f"https://direct.lc.chat/{license_id}/"
                        chat_id = f"{license_id}/{group_id}" if group_id else license_id
                        return LiveChatInfo(
                            base_url="https://direct.lc.chat",
                            chat_id=chat_id,
                            constructed_url=constructed,
                            source=dom_data.get("type", "dom"),
                            provider="livechat",
                            evidence_source=dom_data.get("type", "dom"),
                            account_id=license_id,
                            raw_snippet=outer or target_blob[:200]
                        )
                    # 3. Fallback if livechat detected but license ID not found
                    constructed = src if src.startswith("http") else "https://www.livechat.com"
                    return LiveChatInfo(
                        base_url="https://livechat.com",
                        chat_id="livechat_detected",
                        constructed_url=constructed,
                        source=dom_data.get("type", "dom"),
                        provider="livechat",
                        evidence_source=dom_data.get("type", "dom"),
                        account_id="livechat_detected",
                        raw_snippet=outer or text[:200]
                    )

                # Provider: Widget Embed (e.g. SRGChat, token-based livechat)
                if provider == "widget-embed" or "widget-embed" in target_blob:
                    w_match = re.search(r'(https?://[^"\'\s<>]+)?/widget-embed\?(?:[^"\'\s<>]*&)?token=([a-zA-Z0-9_-]+)', target_blob, re.I)
                    if w_match:
                        matched_base = w_match.group(1)
                        if matched_base:
                            base = matched_base.rstrip('/')
                        elif page:
                            try:
                                parsed = urlparse(page.url)
                                base = f"{parsed.scheme}://{parsed.netloc}"
                            except Exception:
                                base = ""
                        else:
                            base = ""
                        token = w_match.group(2)
                        constructed = f"{base}/widget-embed?token={token}" if base else f"/widget-embed?token={token}"
                        return LiveChatInfo(
                            base_url=base,
                            chat_id=token,
                            constructed_url=constructed,
                            source=dom_data.get("type", "dom"),
                            provider="widget-embed",
                            evidence_source=dom_data.get("type", "dom"),
                            account_id=token,
                            raw_snippet=outer or target_blob[:200]
                        )

                # Provider: Tawk.to
                if provider == "tawk" or "tawk.to" in src or "tawk.to" in text:
                    tawk_m = re.search(r'embed\.tawk\.to/([a-f0-9]{24})/([a-zA-Z0-9_-]+)', src or text, re.I)
                    account_id = tawk_m.group(1) if tawk_m else "tawk_detected"
                    constructed = f"https://tawk.to/chat/{account_id}" if tawk_m else (src if src.startswith("http") else f"https://embed.tawk.to/{account_id}")
                    return LiveChatInfo(
                        base_url="https://tawk.to",
                        chat_id=account_id,
                        constructed_url=constructed,
                        source=dom_data.get("type", "dom"),
                        provider="tawk",
                        evidence_source=dom_data.get("type", "dom"),
                        account_id=account_id,
                        raw_snippet=dom_data.get("outer") or text[:200]
                    )

                # Provider: LiveChat Inc
                if provider == "livechat" or "livechatinc.com" in src or "window.__lc" in text:
                    lic_m = re.search(r'license\s*[:=]\s*["\']?(\d+)["\']?', text)
                    account_id = lic_m.group(1) if lic_m else "livechat_detected"
                    constructed = f"https://direct.lc.chat/{account_id}/" if lic_m else (src if src.startswith("http") else "https://www.livechat.com")
                    return LiveChatInfo(
                        base_url="https://livechat.com",
                        chat_id=account_id,
                        constructed_url=constructed,
                        source=dom_data.get("type", "dom"),
                        provider="livechat",
                        evidence_source=dom_data.get("type", "dom"),
                        account_id=account_id,
                        raw_snippet=dom_data.get("outer") or text[:200]
                    )

                # Provider: Crisp
                if provider == "crisp" or "crisp.chat" in src or "CRISP_WEBSITE_ID" in text:
                    crisp_m = re.search(r'CRISP_WEBSITE_ID\s*=\s*["\']([a-f0-9-]+)["\']', text)
                    account_id = crisp_m.group(1) if crisp_m else "crisp_detected"
                    return LiveChatInfo(
                        base_url="https://crisp.chat",
                        chat_id=account_id,
                        constructed_url=f"https://go.crisp.chat/chat/embed/?website_id={account_id}",
                        source=dom_data.get("type", "dom"),
                        provider="crisp",
                        evidence_source=dom_data.get("type", "dom"),
                        account_id=account_id,
                        raw_snippet=dom_data.get("outer") or text[:200]
                    )

                # Provider: Zendesk
                if provider == "zendesk" or "zdassets.com" in src or "zopim" in text:
                    zd_m = re.search(r'snippet\.js\?key=([a-f0-9-]+)', src)
                    account_id = zd_m.group(1) if zd_m else "zendesk_detected"
                    return LiveChatInfo(
                        base_url="https://zendesk.com",
                        chat_id=account_id,
                        constructed_url=src if src.startswith("http") else "https://www.zendesk.com",
                        source=dom_data.get("type", "dom"),
                        provider="zendesk",
                        evidence_source=dom_data.get("type", "dom"),
                        account_id=account_id,
                        raw_snippet=dom_data.get("outer") or text[:200]
                    )

                # Standard OneChat
                if "index.html?id=" in src:
                    m = re.search(r'(https?://[^"\'\s<>]+)/index\.html\?id=([a-f0-9-]{36}|[A-Za-z0-9_-]+)', src, re.I)
                    if m:
                        base = m.group(1).rstrip('/')
                        cid = m.group(2)
                        lang_m = re.search(r'language=([A-Za-z0-9_-]+)', src)
                        lang = lang_m.group(1) if lang_m else "id"
                        constructed = f"{base}/index.html?id={cid}" + (f"&language={lang}" if lang else "")
                        return LiveChatInfo(
                            base_url=base,
                            chat_id=cid,
                            constructed_url=constructed,
                            language=lang,
                            source=dom_data.get("type", "dom"),
                            provider="onechat",
                            evidence_source=dom_data.get("type", "dom"),
                            account_id=cid,
                            raw_snippet=dom_data.get("outer") or dom_data.get("text")
                        )

                # Parse from script text
                if text:
                    info = _parse_from_script_text(text)
                    if info:
                        info.source = "script_dom"
                        return info
        except Exception:
            pass

    # 2. Analyze raw HTML and inline scripts across frames
    if htmls:
        for html, frame_url in htmls:
            info = _extract_from_html(html)
            if info:
                return info

    return None


def extract_live_chat_info(
    page,
    htmls: Optional[list[tuple[str, str]]] = None,
    expected_brand: Optional[str] = None,
) -> Optional[LiveChatInfo]:
    """Analyze website DOM, scripts, and templates to extract Live Chat ID and base URL.

    Supports OneChat, Tawk.to, LiveChat Inc (direct.lc.chat), Crisp, Zendesk, LivePerson, and Widget-Embed (e.g. SRGChat).
    Constructs canonical or widget URL and gathers structured evidence.
    """
    info = _extract_live_chat_info_core(page, htmls)
    if info and expected_brand:
        brand_cfg = load_brand_livechat_config()
        brand_entry = brand_cfg.get(expected_brand.strip().upper())
        if brand_entry and not info.expected_account_id:
            info.expected_account_id = brand_entry.get("account_id")
    return info


def _parse_from_script_text(text: str) -> Optional[LiveChatInfo]:
    """Parse base URL and chat ID from JavaScript code snippet."""
    # Check direct.lc.chat/<license_id>/<group_id> inside script
    lc_m = re.search(r'direct\.lc\.chat/(\d+)(?:/(\d+))?', text, re.I)
    if lc_m:
        license_id = lc_m.group(1)
        group_id = lc_m.group(2)
        constructed = f"https://direct.lc.chat/{license_id}/{group_id}" if group_id else f"https://direct.lc.chat/{license_id}/"
        chat_id = f"{license_id}/{group_id}" if group_id else license_id
        return LiveChatInfo(
            base_url="https://direct.lc.chat",
            chat_id=chat_id,
            constructed_url=constructed,
            source="script_direct_lc",
            provider="livechat",
            evidence_source="script",
            account_id=license_id,
            raw_snippet=text[:300]
        )

    # Check livechat query param or license config inside script
    if "livechat" in text.lower() or "__lc" in text or "license_id=" in text:
        lic_param = re.search(r'(?:[?&]license_id=|\blicense(?:_id)?\s*[:=]\s*["\']?)(\d+)', text, re.I)
        if lic_param:
            license_id = lic_param.group(1)
            grp_m = re.search(r'(?:[?&]group=|\bgroup\s*[:=]\s*["\']?)(\d+)', text, re.I)
            group_id = grp_m.group(1) if grp_m else None
            constructed = f"https://direct.lc.chat/{license_id}/{group_id}" if group_id else f"https://direct.lc.chat/{license_id}/"
            chat_id = f"{license_id}/{group_id}" if group_id else license_id
            return LiveChatInfo(
                base_url="https://direct.lc.chat",
                chat_id=chat_id,
                constructed_url=constructed,
                source="script_livechat_param",
                provider="livechat",
                evidence_source="script",
                account_id=license_id,
                raw_snippet=text[:300]
            )

    # Check widget-embed?token= inside script
    m_embed = re.search(r'(https?://[^"\'\s<>]+)/widget-embed\?(?:[^"\'\s<>]*&)?token=([a-zA-Z0-9_-]+)', text, re.I)
    if m_embed:
        base = m_embed.group(1).rstrip('/')
        token = m_embed.group(2)
        return LiveChatInfo(
            base_url=base,
            chat_id=token,
            constructed_url=f"{base}/widget-embed?token={token}",
            source="script_widget_embed",
            provider="widget-embed",
            evidence_source="script",
            account_id=token,
            raw_snippet=text[:300]
        )

    # Pattern: id: "0e7861ee-ff6c-4e35-a4df-75b0c096d78b"
    id_match = re.search(r'\bid\s*:\s*["\']([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|[A-Za-z0-9_-]{16,})["\']', text, re.I)
    if not id_match:
        # Check index.html?id= inside script
        m_link = re.search(r'(https?://[^"\'\s<>]+)/index\.html\?id=([a-f0-9-]{36}|[A-Za-z0-9_-]+)', text, re.I)
        if m_link:
            base = m_link.group(1).rstrip('/')
            cid = m_link.group(2)
            lang_m = re.search(r'language=([A-Za-z0-9_-]+)', text)
            lang = lang_m.group(1) if lang_m else "id"
            return LiveChatInfo(
                base_url=base,
                chat_id=cid,
                constructed_url=f"{base}/index.html?id={cid}" + (f"&language={lang}" if lang else ""),
                language=lang,
                source="script_inline_link",
                raw_snippet=text[:300]
            )
        return None

    chat_id = id_match.group(1)

    # Find Base URL
    base_match = re.search(r'(?:BASE_URL|baseUrl)\s*[:=]\s*["\'](https?://[^"\'\s<>]+)["\']', text, re.I)
    if base_match:
        base_url = base_match.group(1).rstrip('/')
    else:
        # Look for script src or general chat domain
        domain_match = re.search(r'["\'](https?://chat\.[^"\'\s<>]+)["\']', text, re.I)
        if domain_match:
            base_url = domain_match.group(1).rstrip('/')
        else:
            base_url = "https://chat.onechat.dev"  # standard fallback for OneChat SDK

    # Language if specified
    lang_match = re.search(r'language\s*[:=]\s*["\']([A-Za-z0-9_-]+)["\']', text, re.I)
    lang = lang_match.group(1) if lang_match else "id"

    constructed = f"{base_url}/index.html?id={chat_id}" + (f"&language={lang}" if lang else "")
    return LiveChatInfo(
        base_url=base_url,
        chat_id=chat_id,
        constructed_url=constructed,
        language=lang,
        source="script_template",
        raw_snippet=text[:300]
    )


def _extract_from_html(html: str) -> Optional[LiveChatInfo]:
    """Scan raw HTML string for Live Chat patterns (links, iframes, SDK snippets)."""
    # Pattern 0a: direct.lc.chat/<license>/<group>
    lc_m = re.search(r'direct\.lc\.chat/(\d+)(?:/(\d+))?', html, re.I)
    if lc_m:
        license_id = lc_m.group(1)
        group_id = lc_m.group(2)
        constructed = f"https://direct.lc.chat/{license_id}/{group_id}" if group_id else f"https://direct.lc.chat/{license_id}/"
        chat_id = f"{license_id}/{group_id}" if group_id else license_id
        return LiveChatInfo(
            base_url="https://direct.lc.chat",
            chat_id=chat_id,
            constructed_url=constructed,
            source="html_direct_lc",
            provider="livechat",
            evidence_source="html",
            account_id=license_id,
            raw_snippet=html[max(0, lc_m.start() - 50):min(len(html), lc_m.end() + 100)]
        )

    # Pattern 0b: livechat query param or license config: license_id=(\d+) / license = (\d+)
    if "livechat" in html.lower() or "__lc" in html or "license_id=" in html:
        lic_param = re.search(r'(?:[?&]license_id=|\blicense(?:_id)?\s*[:=]\s*["\']?)(\d+)', html, re.I)
        if lic_param:
            license_id = lic_param.group(1)
            grp_m = re.search(r'(?:[?&]group=|\bgroup\s*[:=]\s*["\']?)(\d+)', html, re.I)
            group_id = grp_m.group(1) if grp_m else None
            constructed = f"https://direct.lc.chat/{license_id}/{group_id}" if group_id else f"https://direct.lc.chat/{license_id}/"
            chat_id = f"{license_id}/{group_id}" if group_id else license_id
            return LiveChatInfo(
                base_url="https://direct.lc.chat",
                chat_id=chat_id,
                constructed_url=constructed,
                source="html_livechat_param",
                provider="livechat",
                evidence_source="html",
                account_id=license_id,
                raw_snippet=html[max(0, lic_param.start() - 50):min(len(html), lic_param.end() + 100)]
            )

    # Pattern 0b: Direct link, iframe, onclick, or inline script with widget-embed?token=
    m_embed = re.search(r'(https?://[^"\'\s<>]+)/widget-embed\?(?:[^"\'\s<>]*&)?token=([a-zA-Z0-9_-]+)', html, re.I)
    if m_embed:
        base_url = m_embed.group(1).rstrip('/')
        token = m_embed.group(2)
        return LiveChatInfo(
            base_url=base_url,
            chat_id=token,
            constructed_url=f"{base_url}/widget-embed?token={token}",
            source="html_widget_embed",
            provider="widget-embed",
            evidence_source="html",
            account_id=token,
            raw_snippet=html[max(0, m_embed.start() - 50):min(len(html), m_embed.end() + 100)]
        )

    # Pattern 1: Direct link or iframe with index.html?id=
    m_direct = re.search(r'(https?://[^"\'\s<>]+)/index\.html\?id=([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|[A-Za-z0-9_-]{16,})', html, re.I)
    if m_direct:
        base_url = m_direct.group(1).rstrip('/')
        chat_id = m_direct.group(2)
        lang_match = re.search(rf'{re.escape(chat_id)}[^"\'\s<>]*language=([A-Za-z0-9_-]+)', html, re.I)
        lang = lang_match.group(1) if lang_match else "id"
        constructed = f"{base_url}/index.html?id={chat_id}" + (f"&language={lang}" if lang else "")
        return LiveChatInfo(
            base_url=base_url,
            chat_id=chat_id,
            constructed_url=constructed,
            language=lang,
            source="html_pattern",
            raw_snippet=html[max(0, m_direct.start() - 50):min(len(html), m_direct.end() + 100)]
        )

    # Pattern 2: Script blocks with WebSDK / BASE_URL
    for script_match in re.finditer(r'<script[\s\S]*?</script>', html, re.I):
        script_content = script_match.group(0)
        if any(k in script_content for k in ('WebSDK', 'BASE_URL', 'baseUrl', 'onechat')):
            info = _parse_from_script_text(script_content)
            if info:
                return info

    return None


def verify_live_chat_page(
    context,
    chat_url: str,
    expected_brand: Optional[str] = None,
    live_chat_info: Optional[LiveChatInfo] = None,
) -> LiveChatResult:
    """Open the constructed Live Chat URL and verify if it is an actual Live Chat page.

    Checks:
    - HTTP response and load status
    - Error messages: 'Invalid ID', 'Please check the URL'
    - Presence of chat interface: conversation buttons, headers, greeting text
    - Optional match against expected brand name and configured license account ID
    """
    # Look up expected_account_id from brand config if not already provided
    brand_cfg = load_brand_livechat_config()
    expected_acc = live_chat_info.expected_account_id if live_chat_info else None
    if expected_brand and not expected_acc:
        brand_entry = brand_cfg.get(expected_brand.strip().upper())
        if brand_entry:
            expected_acc = brand_entry.get("account_id")
            if live_chat_info:
                live_chat_info.expected_account_id = expected_acc

    # If brand config defines an expected account ID, enforce strict match:
    if expected_acc and live_chat_info and live_chat_info.account_id:
        acc_str = str(live_chat_info.account_id).strip()
        exp_str = str(expected_acc).strip()
        if acc_str != exp_str:
            return LiveChatResult(
                is_live_chat=False,
                status="ACCOUNT MISMATCH",
                live_chat_info=live_chat_info,
                page_title="",
                brand_matched=False,
                details=f"Live Chat license account mismatch for {expected_brand.upper()}: expected '{exp_str}', detected '{acc_str}'.",
                provider_evidence={
                    "provider": live_chat_info.provider,
                    "detected": True,
                    "account_id": acc_str,
                    "expected_account_id": exp_str,
                    "match": False,
                    "evidence_source": live_chat_info.evidence_source,
                }
            )

    provider_evidence = {
        "provider": live_chat_info.provider if live_chat_info else "unknown",
        "detected": True if live_chat_info else False,
        "account_id": live_chat_info.account_id if live_chat_info else None,
        "expected_account_id": expected_acc,
        "match": (str(live_chat_info.account_id).strip() == str(expected_acc).strip()) if (live_chat_info and expected_acc) else None,
        "evidence_source": live_chat_info.evidence_source if live_chat_info else "unknown",
    }

    page = context.new_page()
    try:
        try:
            resp = page.goto(chat_url, wait_until="networkidle", timeout=15000)
        except Exception:
            try:
                resp = page.goto(chat_url, wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(2500)
            except Exception as exc:
                return LiveChatResult(
                    is_live_chat=False,
                    status="ERROR",
                    live_chat_info=live_chat_info,
                    details=f"Failed to load Live Chat URL: {exc}",
                    provider_evidence=provider_evidence,
                )

        status_code = resp.status if resp else 0
        if status_code >= 400:
            return LiveChatResult(
                is_live_chat=False,
                status="HTTP ERROR",
                live_chat_info=live_chat_info,
                details=f"Live Chat URL returned HTTP {status_code}",
                provider_evidence=provider_evidence,
            )

        title = (page.title() or "").strip()
        body_text = ""
        try:
            body_text = page.inner_text("body", timeout=5000).strip()
        except Exception:
            body_text = page.content()

        body_lower = body_text.lower()

        # Check 1: Explicit invalid ID message
        if "invalid id" in body_lower or "please check the url" in body_lower:
            return LiveChatResult(
                is_live_chat=False,
                status="INVALID ID",
                live_chat_info=live_chat_info,
                page_title=title,
                greeting_text=body_text[:200],
                details="Live Chat service returned 'Invalid ID. Please check the URL.'",
                provider_evidence=provider_evidence,
            )

        # Check 2: Extract buttons and interactive elements
        buttons = []
        try:
            btn_locs = page.locator("button, a.button, [role='button']").all_inner_texts()
            buttons = [b.strip() for b in btn_locs if b.strip()]
        except Exception:
            pass

        # Check 3: Live Chat indicators
        chat_keywords = [
            "mulai percakapan", "start conversation", "tidak ada percakapan",
            "percakapan baru", "pelayanan 24", "melayani 24", "siap membantu",
            "hubungi kami", "selamat datang", "livechat", "live chat", "webwidget",
            "obrolan langsung", "live help", "customer service", "cs online",
            "kami online", "we are online", "tinggalkan pesan", "kirim pesan",
            "tulis pesan", "ketik pesan", "type a message", "send a message", "masukkan pesan"
        ]
        matched_indicators = [k for k in chat_keywords if k in body_lower or k in title.lower()]
        has_chat_button = any(
            any(k in b.lower() for k in ("mulai percakapan", "start conversation", "chat", "kirim", "send", "obrolan", "help"))
            for b in buttons
        )
        is_webwidget = (
            "webwidget" in title.lower()
            or "webwidget" in body_lower
            or "widget-embed" in chat_url.lower()
            or "direct.lc.chat" in chat_url.lower()
            or "lc.chat" in chat_url.lower()
        )

        has_chat_input = False
        try:
            has_chat_input = page.locator("textarea, input[placeholder*='pesan' i], input[placeholder*='message' i], input[placeholder*='chat' i]").count() > 0
        except Exception:
            pass

        # Brand name match check
        brand_matched = False
        if expected_acc and live_chat_info and live_chat_info.account_id:
            if str(live_chat_info.account_id).strip() == str(expected_acc).strip():
                brand_matched = True
        elif expected_brand:
            brand_norm = expected_brand.strip().lower()
            if brand_norm and brand_norm in body_lower:
                brand_matched = True

        # Decision
        if (matched_indicators or has_chat_button or is_webwidget or has_chat_input) and "invalid id" not in body_lower:
            return LiveChatResult(
                is_live_chat=True,
                status="VERIFIED",
                live_chat_info=live_chat_info,
                page_title=title,
                greeting_text=body_text[:300],
                buttons=buttons,
                brand_matched=brand_matched,
                details=f"Live Chat verified. Active indicators: {', '.join(matched_indicators[:3]) if matched_indicators else 'Chat Interface Active'}.",
                provider_evidence=provider_evidence,
            )
        else:
            # If expected account ID is unavailable, report UNKNOWN instead of false phishing
            fallback_status = "UNKNOWN" if (live_chat_info and live_chat_info.expected_account_id is None and live_chat_info.provider not in ("onechat", "widget-embed", "livechat")) else "RANDOM PAGE"
            return LiveChatResult(
                is_live_chat=False,
                status=fallback_status,
                live_chat_info=live_chat_info,
                page_title=title,
                greeting_text=body_text[:200],
                buttons=buttons,
                brand_matched=brand_matched,
                details="Page does not match Live Chat interface characteristics or expected ID was unavailable.",
                provider_evidence=provider_evidence,
            )
    finally:
        try:
            page.close()
        except Exception:
            pass


def format_livechat_output(info: Optional[LiveChatInfo], result: LiveChatResult, colorize: bool = True) -> str:
    """Format the Live Chat identification and verification output."""
    lines = []
    c_cyan = ANSI_BOLD_CYAN if colorize else ""
    c_green = ANSI_BOLD_GREEN if colorize else ""
    c_red = ANSI_BOLD_RED if colorize else ""
    c_yellow = ANSI_BOLD_YELLOW if colorize else ""
    c_reset = ANSI_RESET if colorize else ""

    lines.append(f"{c_cyan}=== [Live Chat Verification] ==={c_reset}")
    if info:
        lines.append(f"  Live Chat ID    : {info.chat_id}")
        lines.append(f"  Base URL        : {info.base_url}")
        lines.append(f"  Constructed URL : {info.constructed_url}")
        lines.append(f"  Source          : {info.source}")
    else:
        lines.append(f"  {c_red}Live Chat Link  : NOT FOUND in script/template{c_reset}")

    if result.is_live_chat:
        status_color = c_green
        lines.append(f"  Status          : {status_color}[ACTUAL LIVE CHAT VERIFIED]{c_reset}")
    else:
        status_color = c_red
        lines.append(f"  Status          : {status_color}[{result.status} - NOT GENUINE CHAT]{c_reset}")

    if result.page_title:
        lines.append(f"  Page Title      : {result.page_title}")
    if result.brand_matched:
        lines.append(f"  Brand Confirmed : {c_green}YES (Brand name identified inside Live Chat greeting){c_reset}")
    if result.greeting_text:
        snippet = result.greeting_text.replace('\n', ' ')
        lines.append(f"  Chat Preview    : {snippet[:120]}...")
    if result.details:
        lines.append(f"  Details         : {result.details}")
    lines.append(f"{c_cyan}================================{c_reset}")

    return "\n".join(lines)
