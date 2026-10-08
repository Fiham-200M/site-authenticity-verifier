"""Pydantic v2 data models for end-link-lc crawler.

Ensures strict internal data validation while maintaining 100% backward
compatibility with existing report.json and metadata.json schemas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class BrandMatch(BaseModel):
    """Represents a brand match result with forensic tracking."""
    brand: str
    match_method: str = Field(
        default="exact",
        description="Method used for matching: exact, normalized, homoglyph, or fuzzy",
    )
    similarity: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Similarity ratio (1.0 for exact/homoglyph, <1.0 for fuzzy)",
    )
    occurrences: int = Field(default=1, ge=0)


class LogoForensics(BaseModel):
    """Rich forensic signals returned by the AI vision verification service."""
    verdict: str = Field(default="UNKNOWN", description="MATCH, REVIEW, or UNKNOWN")
    brand_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    asset_type: str = Field(default="unknown")
    threat_type: Optional[str] = None
    reason: str = Field(default="")
    processing_ms: int = Field(default=0, ge=0)
    siglip_score: Optional[float] = None
    dino_score: Optional[float] = None
    delta_e: Optional[float] = None
    edge_iou: Optional[float] = None
    ocr_match: Optional[bool] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    cached: bool = Field(default=False)
    sha256: Optional[str] = None
    error: Optional[str] = None


class LiveChatForensics(BaseModel):
    """Live chat detection and verification evidence."""
    provider: Optional[str] = None
    detected: bool = Field(default=False)
    account_id: Optional[str] = None
    expected_account_id: Optional[str] = None
    match: Optional[bool] = None
    evidence_source: str = Field(default="unknown")  # network, dom, iframe, script
    status: str = Field(default="NOT FOUND")
    page_title: str = Field(default="")
    constructed_url: Optional[str] = None
    details: str = Field(default="")
    raw_evidence: Optional[str] = None


class ContentClassification(BaseModel):
    """Content intelligence categorization independent of logo verification."""
    categories: List[str] = Field(default_factory=list)
    primary_category: str = Field(default="OTHER")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    keywords: List[str] = Field(default_factory=list)
    signals: Dict[str, Any] = Field(default_factory=dict)


class CloakingResult(BaseModel):
    """Evidence of suspicious rendering divergence between viewports."""
    detected: bool = Field(default=False)
    reason: str = Field(default="")
    desktop_signals: List[str] = Field(default_factory=list)
    mobile_signals: List[str] = Field(default_factory=list)
    cta_diff: List[str] = Field(default_factory=list)


class PerformanceMetrics(BaseModel):
    """Performance and latency telemetry for crawl operations."""
    navigation_ms: int = Field(default=0)
    asset_extraction_ms: int = Field(default=0)
    ai_verification_ms: int = Field(default=0)
    total_ms: int = Field(default=0)
    blocked_requests_count: int = Field(default=0)
    blocked_bytes: int = Field(default=0)
    ai_cache_hits: int = Field(default=0)


class PageRecord(BaseModel):
    """Record for an individual visited page in report.json."""
    requested_url: str
    url: Optional[str] = None
    folder: Optional[str] = None
    classification: Optional[str] = None
    page_type: Optional[str] = None
    page_type_reason: Optional[str] = None
    page_type_signals: Dict[str, Any] = Field(default_factory=dict)
    links: List[str] = Field(default_factory=list)
    url_counts: Dict[str, int] = Field(default_factory=dict)
    brand_matches: List[str] = Field(default_factory=list)
    page_dominant_brand: Optional[str] = None
    selected_brand: Optional[str] = None
    brand_counts: Dict[str, int] = Field(default_factory=dict)
    logo_identified: Optional[bool] = None
    yes_streak: int = Field(default=0)
    no_streak: int = Field(default=0)
    money_confirmation: Optional[int] = None
    live_chat_info: Optional[Dict[str, Any]] = None
    live_chat_verification: Optional[Dict[str, Any]] = None
    rebrandly_matches: List[Dict[str, Any]] = Field(default_factory=list)
    total_urls: int = Field(default=0)
    total_url_occurrences: int = Field(default=0)
    duplicate_url_occurrences: int = Field(default=0)
    frame_count: int = Field(default=0)

    # Enhanced additive fields (non-breaking)
    brand_detection_detail: Optional[BrandMatch] = None
    logo_forensics: Optional[LogoForensics] = None
    content_intelligence: Optional[ContentClassification] = None
    cloaking: Optional[CloakingResult] = None
    performance: Optional[PerformanceMetrics] = None

    def to_report_dict(self) -> Dict[str, Any]:
        """Convert to dict preserving exact backward-compatible report structure."""
        data = self.model_dump(exclude_none=False)
        # Flatten nested models into dicts where needed
        return data


class CrawlReport(BaseModel):
    """Top-level report schema for report.json."""
    start_url: str
    brands: List[str] = Field(default_factory=list)
    classifier: str = Field(default="manual")
    classification: str = Field(default="INCONCLUSIVE")
    pages: List[Dict[str, Any]] = Field(default_factory=list)
    redirects: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    rebrandly_matches: List[Dict[str, Any]] = Field(default_factory=list)
    live_chat_info: Optional[Dict[str, Any]] = None
    live_chat_verification: Optional[Dict[str, Any]] = None
    selected_brand: Optional[str] = None
    money_confirmations: int = Field(default=0)
    money_confirmations_required: int = Field(default=1)
    pending_count: int = Field(default=0)
    total_unique_urls: int = Field(default=0)
    visited_url_count: int = Field(default=0)
    navigation_attempts: int = Field(default=0)
    checked_page_count: int = Field(default=0)
    classification_scope: str = Field(default="")
    status: str = Field(default="IN PROGRESS")
    max_pages: int = Field(default=100)
    stop_on_logo_missing: bool = Field(default=True)

    # Additive forensic and telemetry summary
    cloaking_summary: Optional[Dict[str, Any]] = None
    performance_summary: Optional[Dict[str, Any]] = None

    def to_report_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=False)
