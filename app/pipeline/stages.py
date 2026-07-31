"""Canonical pipeline stage definitions.

Single source of truth for stage identity, ordering, and display text. The
frontend fetches these from ``GET /api/pipelines/{kind}/stages`` instead of
maintaining its own copy of the list.
"""

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    """Coarse job-level state. Stage rows carry the detail."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    """Report was produced but delivery failed - artifacts are still downloadable."""
    FAILED = "failed"
    """No report was produced."""
    INTERRUPTED = "interrupted"
    """Process died mid-run; set by startup recovery."""


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.INTERRUPTED}
)


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorKind(StrEnum):
    """Why a stage failed. Drives the message the user sees."""

    PARSE_ERROR = "parse_error"
    """Model output could not be parsed into the target schema."""
    VALIDATION_ERROR = "validation_error"
    """Parsed, but failed Pydantic validation."""
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    """Non-2xx from an upstream HTTP call (scrape target or LLM endpoint)."""
    UPSTREAM_ERROR = "upstream_error"
    """Transport-level failure: connection reset, DNS, TLS."""
    BLOCKED = "blocked"
    """Refused by policy, e.g. the SSRF guard."""
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Stage:
    slug: str
    display: str
    """Stable name used in logs and metrics."""
    label: str
    """Editorial voice, shown to the user."""
    seq: int
    uses_llm: bool


BI_STAGES: tuple[Stage, ...] = (
    Stage("scrape", "Scraper", "Scraping the site", 0, uses_llm=False),
    Stage("profile", "Business Profile", "Reading the business", 1, uses_llm=True),
    Stage("market", "Market Analysis", "Sizing the market", 2, uses_llm=True),
    Stage("competitive", "Competitive Analysis", "Mapping competitors", 3, uses_llm=True),
    Stage("swot", "SWOT Analysis", "Building the SWOT", 4, uses_llm=True),
    Stage("marketing", "Marketing Analysis", "Drafting the marketing view", 5, uses_llm=True),
    Stage("report", "Report Writer", "Writing the report", 6, uses_llm=True),
    Stage("render", "Render", "Rendering PDF and DOCX", 7, uses_llm=False),
    Stage("deliver", "Email Delivery", "Sending it to you", 8, uses_llm=False),
)

MARKET_RESEARCH_STAGES: tuple[Stage, ...] = (
    Stage("macro", "Macro Analysis", "Scanning the macro environment", 0, uses_llm=True),
    Stage("sizing", "Market Sizing", "Sizing the opportunity", 1, uses_llm=True),
    Stage("resources", "Resource Compilation", "Gathering sources", 2, uses_llm=True),
    Stage("render", "Render", "Rendering PDF and DOCX", 3, uses_llm=False),
)

PIPELINES: dict[str, tuple[Stage, ...]] = {
    "bi": BI_STAGES,
    "market_research": MARKET_RESEARCH_STAGES,
}


def stages_for(kind: str) -> tuple[Stage, ...]:
    """Ordered stages for a pipeline kind. Raises KeyError for unknown kinds."""
    return PIPELINES[kind]


def stage_by_slug(kind: str, slug: str) -> Stage:
    """Look up one stage. Raises KeyError if the kind or slug is unknown."""
    for stage in stages_for(kind):
        if stage.slug == slug:
            return stage
    raise KeyError(f"unknown stage {slug!r} for pipeline {kind!r}")
