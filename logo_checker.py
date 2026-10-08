"""
==========================================================================
 Logo & Favicon Forensic Checker - Crawler Integration Module
==========================================================================
 Drop this file into your crawler project.
 Usage: see bottom of file or README_INTEGRATION.md

 API Server  : http://192.168.10.113:8000
 Endpoint    : POST /api/verify
 Timeout     : 30 seconds per image
==========================================================================
"""

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional, Union, Dict, Any
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


# ---------------------------------------------------------------------------
# CONFIGURATION & DISCOVERY
# ---------------------------------------------------------------------------

DEFAULT_API_HOSTS = [
    os.environ.get("LOGO_API_URL"),
    "http://127.0.0.1:8000/api/verify",
    "http://192.168.10.113:8000/api/verify",
]
API_URL = next((h for h in DEFAULT_API_HOSTS if h), "http://127.0.0.1:8000/api/verify")
TIMEOUT     = 30   # seconds
MAX_RETRIES = 2    # retries on network error


# ---------------------------------------------------------------------------
# ASSET DEDUPLICATION & IN-MEMORY CACHE
# ---------------------------------------------------------------------------

_AI_VERIFY_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_STATS = {"hits": 0, "misses": 0}


def get_cache_stats() -> Dict[str, int]:
    """Return hit and miss telemetry for the AI request cache."""
    return dict(_CACHE_STATS, total_cached=len(_AI_VERIFY_CACHE))


def clear_cache() -> None:
    """Clear in-memory request cache."""
    _AI_VERIFY_CACHE.clear()
    _CACHE_STATS["hits"] = 0
    _CACHE_STATS["misses"] = 0


# ---------------------------------------------------------------------------
# VERDICT CONSTANTS
# ---------------------------------------------------------------------------

VERDICT_MATCH   = "MATCH"    # Confirmed: This IS our logo / favicon
VERDICT_REVIEW  = "REVIEW"   # Suspicious: possible clone, needs human check
VERDICT_UNKNOWN = "UNKNOWN"  # Not our logo / favicon


# ---------------------------------------------------------------------------
# CORE FUNCTION  --  call this from your crawler loop
# ---------------------------------------------------------------------------

def check_image(image_source, filename="image.png", asset_mode="auto"):
    """
    Check whether an image is one of our official logos or favicons.
    Preserves multi-modal forensic metrics (SigLIP, DINO, OCR, Color Delta-E).
    """
    global API_URL
    raw_bytes, error = _load_image(image_source, filename)
    if error:
        return _error_result(error, error_code="LOAD_ERROR")

    # In-memory deduplication cache check (SHA-256)
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
    cache_key = f"{sha256_hash}_{asset_mode}"
    if cache_key in _AI_VERIFY_CACHE:
        _CACHE_STATS["hits"] += 1
        cached_result = dict(_AI_VERIFY_CACHE[cache_key])
        cached_result["cached"] = True
        return cached_result

    _CACHE_STATS["misses"] += 1
    b64_str = "data:image/png;base64," + base64.b64encode(raw_bytes).decode()

    payload = json.dumps({
        "image_base64": b64_str,
        "filename":     filename,
        "asset_mode":   asset_mode,
        "debug":        False,
        "skip_vlm":     True,
    }).encode()

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            t0  = time.time()
            req = Request(API_URL, data=payload,
                          headers={"Content-Type": "application/json"},
                          method="POST")
            with urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read())
            ms  = int((time.time() - t0) * 1000)

            report  = data.get("report", {})
            verdict = report.get("verdict", "UNKNOWN")

            # Preserve comprehensive forensic evidence
            result = {
                "is_our_logo":   verdict == VERDICT_MATCH,
                "verdict":       verdict,
                "brand":         report.get("brand_id"),
                "confidence":    report.get("confidence_score", 0.0),
                "asset_type":    report.get("asset_type", asset_mode),
                "threat_type":   report.get("threat_type"),
                "reason":        report.get("action_reason", ""),
                "processing_ms": ms,
                "siglip_score":  report.get("siglip") or report.get("siglip_score"),
                "dino_score":    report.get("dino") or report.get("dino_score"),
                "delta_e":       report.get("delta_e"),
                "edge_iou":      report.get("edge_iou"),
                "ocr_match":     report.get("ocr_match"),
                "metrics":       report.get("metrics", {}),
                "cached":        False,
                "sha256":        sha256_hash,
                "error":         None,
            }
            # Cache the successful forensic result
            _AI_VERIFY_CACHE[cache_key] = result
            return result

        except (TimeoutError, URLError) as exc:
            err_str = str(exc)
            if "timed out" in err_str.lower():
                last_error = ("AI_TIMEOUT", f"API request timed out: {exc}")
            else:
                last_error = ("AI_UNAVAILABLE", f"API service unreachable ({API_URL}): {exc}")

            if attempt < MAX_RETRIES:
                # Try fallback host if first host is unreachable
                if attempt == 0 and "127.0.0.1" in API_URL:
                    API_URL = "http://192.168.10.113:8000/api/verify"
                elif attempt == 0 and "192.168.10.113" in API_URL:
                    API_URL = "http://127.0.0.1:8000/api/verify"
                time.sleep(0.5 * (2 ** attempt))
                continue
            res = _error_result(last_error[1], error_code=last_error[0], sha256=sha256_hash)
            _AI_VERIFY_CACHE[cache_key] = res
            return res

        except (json.JSONDecodeError, KeyError) as exc:
            res = _error_result(f"Invalid API response: {exc}", error_code="AI_INVALID_RESPONSE", sha256=sha256_hash)
            _AI_VERIFY_CACHE[cache_key] = res
            return res

        except Exception as exc:
            res = _error_result(f"Unexpected API error: {exc}", error_code="AI_ERROR", sha256=sha256_hash)
            _AI_VERIFY_CACHE[cache_key] = res
            return res

    err_code, err_msg = last_error if last_error else ("AI_ERROR", "Unknown error")
    res = _error_result(err_msg, error_code=err_code, sha256=sha256_hash)
    _AI_VERIFY_CACHE[cache_key] = res
    return res


# ---------------------------------------------------------------------------
# BATCH HELPER  --  check logo + favicon of a web page in one call
# ---------------------------------------------------------------------------

def check_page_assets(page_url, logo_url=None, favicon_url=None):
    """
    Check both the logo and favicon of a web page.

    Parameters
    ----------
    page_url    : str  - the page URL (used for logging)
    logo_url    : str  - URL of the main logo image  (optional)
    favicon_url : str  - URL of the favicon           (optional)

    Returns
    -------
    dict with keys:
        page_url    str  - the page URL
        logo        dict - check_image() result for logo, or None
        favicon     dict - check_image() result for favicon, or None
        verdict     str  - "MATCH" | "REVIEW" | "UNKNOWN"
        is_our_page bool - True if logo or favicon is a MATCH

    Example
    -------
    result = check_page_assets(
        page_url    = "https://example-site.com",
        logo_url    = "https://example-site.com/assets/logo.png",
        favicon_url = "https://example-site.com/favicon.ico",
    )
    print(result["verdict"])
    """

    domain         = page_url.split("//")[-1].split("/")[0]
    logo_result    = check_image(logo_url,    f"{domain}_logo.png",    "logo")    if logo_url    else None
    favicon_result = check_image(favicon_url, f"{domain}_favicon.png", "favicon") if favicon_url else None

    results = [r for r in [logo_result, favicon_result] if r]
    if any(r["verdict"] == VERDICT_MATCH   for r in results): overall = "MATCH"
    elif any(r["verdict"] == VERDICT_REVIEW for r in results): overall = "REVIEW"
    else:                                                       overall = "UNKNOWN"

    return {
        "page_url":    page_url,
        "logo":        logo_result,
        "favicon":     favicon_result,
        "verdict":     overall,
        "is_our_page": overall == "MATCH",
    }


# ---------------------------------------------------------------------------
# FULL SCREENSHOT SCANNER HELPER  --  check full-page screenshots
# ---------------------------------------------------------------------------

def check_screenshot(image_source, filename="screenshot.png"):
    """
    Scan an entire webpage screenshot.
    Automatically finds, crops, and verifies our brand logos across the page.

    Parameters
    ----------
    image_source : str | bytes | Path
        - str starting with http/https -> downloads screenshot from URL
        - bytes / bytearray            -> raw screenshot image bytes
        - Path / file path str         -> local screenshot file path

    filename : str
        Filename for logging (e.g. "page_screenshot.png")

    Returns
    -------
    dict with keys:
        is_our_logo      bool  - True if our brand logo was detected on the page
        verdict          str   - "MATCH" | "REVIEW" | "UNKNOWN"
        brand            str   - detected brand name, or None
        confidence       float - 0.0 to 1.0 confidence score
        detections       list  - list of detected regions with bounding boxes [x, y, w, h]
        detections_count int   - number of brand regions located
        processing_ms    int   - elapsed time in milliseconds
        error            str   - error message if call failed, else None
    """
    raw_bytes, error = _load_image(image_source, filename)
    if error:
        return _error_result(error, error_code="LOAD_ERROR")

    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
    cache_key = f"{sha256_hash}_screenshot"
    if cache_key in _AI_VERIFY_CACHE:
        _CACHE_STATS["hits"] += 1
        cached_result = dict(_AI_VERIFY_CACHE[cache_key])
        cached_result["cached"] = True
        return cached_result

    _CACHE_STATS["misses"] += 1
    b64_str = "data:image/png;base64," + base64.b64encode(raw_bytes).decode()
    payload = json.dumps({
        "image_base64": b64_str,
        "filename":     filename,
    }).encode()

    screenshot_api_url = API_URL.replace("/api/verify", "/api/verify_screenshot")

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            t0 = time.time()
            req = Request(screenshot_api_url, data=payload,
                          headers={"Content-Type": "application/json"},
                          method="POST")
            with urlopen(req, timeout=TIMEOUT * 2) as resp:
                data = json.loads(resp.read())
            ms = int((time.time() - t0) * 1000)

            report = data.get("report", {})
            verdict = report.get("verdict", "UNKNOWN")
            result = {
                "is_our_logo":      verdict == VERDICT_MATCH,
                "verdict":          verdict,
                "brand":            report.get("brand_id"),
                "confidence":       report.get("confidence_score", 0.0),
                "asset_type":       "screenshot",
                "detections":       report.get("detections", []),
                "detections_count": report.get("detections_count", 0),
                "threat_type":      report.get("threat_type"),
                "reason":           report.get("action_reason", ""),
                "processing_ms":    ms,
                "metrics":          report.get("metrics", {}),
                "cached":           False,
                "sha256":           sha256_hash,
                "error":            None,
            }
            _AI_VERIFY_CACHE[cache_key] = result
            return result

        except (TimeoutError, URLError) as exc:
            err_str = str(exc)
            if "timed out" in err_str.lower():
                last_error = ("AI_TIMEOUT", f"Screenshot API timed out: {exc}")
            else:
                last_error = ("AI_UNAVAILABLE", f"Screenshot API unreachable: {exc}")

            if attempt < MAX_RETRIES:
                time.sleep(0.5 * (2 ** attempt))
                continue
            return _error_result(last_error[1], error_code=last_error[0], sha256=sha256_hash)

        except (json.JSONDecodeError, KeyError) as exc:
            return _error_result(f"Invalid API response: {exc}", error_code="AI_INVALID_RESPONSE", sha256=sha256_hash)

        except Exception as exc:
            return _error_result(f"Unexpected screenshot API error: {exc}", error_code="AI_ERROR", sha256=sha256_hash)

    err_code, err_msg = last_error if last_error else ("AI_ERROR", "Unknown error")
    return _error_result(err_msg, error_code=err_code, sha256=sha256_hash)


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _load_image(source, filename):
    """Load image bytes from URL, bytes, or Path. Returns (bytes, error_str)."""
    try:
        if isinstance(source, (bytes, bytearray)):
            return bytes(source), None

        if isinstance(source, str) and source.startswith(("http://", "https://")):
            req = Request(source, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as r:
                return r.read(), None

        path = Path(source)
        return path.read_bytes(), None

    except Exception as exc:
        return None, f"Failed to load image '{filename}': {exc}"


def _error_result(message, error_code="ERROR", sha256=None):
    return {
        "is_our_logo":   False,
        "verdict":       "UNKNOWN",
        "brand":         None,
        "confidence":    0.0,
        "asset_type":    "unknown",
        "threat_type":   None,
        "reason":        message,
        "processing_ms": 0,
        "error":         error_code,
        "error_details": message,
        "sha256":        sha256,
        "cached":        False,
    }


# ---------------------------------------------------------------------------
# STANDALONE CONNECTION TEST  --  run:  python logo_checker.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    HEALTH_URL = API_URL.replace("/api/verify", "/api/health")

    print("=" * 60)
    print("  Logo Forensic API - Connection Test")
    print("=" * 60)

    # 1. Health check
    try:
        with urlopen(Request(HEALTH_URL), timeout=5) as r:
            health = json.loads(r.read())
        print(f"  Server     : ONLINE  ({API_URL})")
        print(f"  Brands     : {health.get('brands_count')} protected")
        print(f"  Device     : {str(health.get('device','')).upper()}")
        print(f"  Status     : {health.get('status')}")
    except Exception as exc:
        print(f"  CANNOT CONNECT to {API_URL}")
        print(f"  Error: {exc}")
        print(f"\n  Make sure the server is running:")
        print(f"    python web_server.py 8000")
        raise SystemExit(1)

    print()

    # 2. Sample image test
    sample = Path(__file__).parent.parent / "Favicon" / "023_raja100-top.com_favicon.png"
    if sample.exists():
        print(f"  Testing favicon: {sample.name}")
        r = check_image(sample, sample.name, "favicon")
        print(f"  verdict     : {r['verdict']}")
        print(f"  brand       : {r['brand']}")
        print(f"  confidence  : {r['confidence']:.1%}")
        print(f"  is_our_logo : {r['is_our_logo']}")
        print(f"  time_ms     : {r['processing_ms']} ms")
    else:
        print("  (sample favicon not found - skipping image test)")

    print()
    print("  All OK. logo_checker.py is ready to use in your crawler.")
    print("=" * 60)
