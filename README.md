# Site Authenticity Verifier

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-1.62.0-2EAD33)
![Forensics](https://img.shields.io/badge/AI%20Forensics-SigLIP2%20%7C%20DINOv2-blue)

**Trace website destinations. Identify brands. Verify authentic sites vs. phishing copies.**

A browser-based crawler and forensic analysis engine that follows redirects, discovers navigation paths, reaches destination portals, and verifies site authenticity using:
1. **Multi-modal Visual Forensics**: Automated AI verification of official logos and favicons (SigLIP 2, DINOv2, OCR consensus, color Delta-E).
2. **Partner Live Chat Verification**: License and group ID validation for embedded chat providers (LiveChat Inc, OneChat, Tawk.to, Crisp, Zendesk, Widget-Embed).
3. **Behavioral & Structural Inspection**: AMP vs. Money Site detection, cloaking divergence inspection, and Rebrandly tracking.

## Quick Start

Run these commands in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the crawler:

```powershell
.\.venv\Scripts\python.exe crawler.py "https://example.com"
```

## Current Workflow

1. **Discovery & Navigation**: Open starting URL, resolve doorway / landing redirects, and inspect rendered DOM and frames.
2. **Brand Detection**: Detect dominant brand mentions using `brand.json`.
3. **Asset Forensics**: Extract and verify logos and favicons against authentic brand gallery.
4. **Live Chat Verification**: Inspect chat widgets, validate license IDs against `brand_livechat_config.json`, and verify active support headers.
5. **Classification**:
   - `OUR SITE`: All assets (Logo, Favicon, Live Chat) verified authentic.
   - `<BRAND> PHISHING`: Asset mismatch or unverified clone detected.
   - `TEMPROVERLY STRAY DOMAIN`: No protected brand identified on doorway.

## Output

```text
Output/
└── crawl_<timestamp>/
    ├── report.json
    └── <domain>_<timestamp>/
        ├── content.txt
        ├── logo.<ext>
        ├── favicon.<ext>
        └── metadata.json
```
