"""Remaining-time estimates from recorded stage durations.

Durations only became available once stages were persisted, so this is a
straight read of history: the median of recent successful runs for each
(stage, model) pair, summed over whatever has not finished yet.

Medians, not means - one 90-second timeout should not distort the estimate.
"""

import logging
from statistics import median
from typing import Any, Protocol

from app.analysis.specs import BI_SPECS, MARKET_RESEARCH_SPECS
from app.pipeline.stages import StageStatus, stages_for

logger = logging.getLogger(__name__)

MIN_SAMPLES = 3
"""Below this, history is too thin to show a number to the user."""

SAMPLE_WINDOW = 20


class _DurationSource(Protocol):
    async def recent_durations(
        self, wanted: list[tuple[str, str]], limit: int = 20
    ) -> dict[str, list[int]]: ...


def _models_by_stage(kind: str) -> dict[str, str]:
    specs = BI_SPECS if kind == "bi" else MARKET_RESEARCH_SPECS
    return {spec.key: spec.model for spec in specs}


async def estimate_remaining_ms(
    store: _DurationSource, kind: str, stage_rows: list[dict[str, Any]]
) -> int | None:
    """Milliseconds still expected, or None when history is too thin.

    Mechanical stages (scrape, render, deliver) have no configured model, so
    their durations are looked up under an empty model key - which is exactly
    how they were recorded.
    """
    # A failed stage has finished too - it is not coming back, so counting it
    # as still-to-come inflated the estimate for every job that broke.
    finished = {
        row["stage"]
        for row in stage_rows
        if row.get("status")
        in (StageStatus.SUCCEEDED, StageStatus.SKIPPED, StageStatus.FAILED)
    }
    models = _models_by_stage(kind)
    pending = [stage.slug for stage in stages_for(kind) if stage.slug not in finished]
    if not pending:
        return 0

    # One lookup for every unfinished stage; see JobStore.recent_durations.
    try:
        samples = await store.recent_durations(
            [(slug, models.get(slug, "")) for slug in pending], SAMPLE_WINDOW
        )
    except Exception:  # noqa: BLE001 - an estimate is never worth failing a request
        logger.debug("Duration lookup failed", exc_info=True)
        return None

    observed = [d for durations in samples.values() for d in durations]
    if len(observed) < MIN_SAMPLES:
        return None

    # A stage with no history of its own borrows the typical stage duration,
    # so one new stage does not suppress the whole estimate.
    fallback = median(observed)
    total = sum(
        median(durations) if len(durations) >= MIN_SAMPLES else fallback
        for durations in samples.values()
    )
    return int(total)


def elapsed_ms(stage_rows: list[dict[str, Any]]) -> int:
    """Total recorded time across finished stages."""
    return sum(int(row["duration_ms"] or 0) for row in stage_rows if row.get("duration_ms"))


def _weighted_mean(scored: list[tuple[float, float]]) -> float | None:
    """Duration-weighted mean of (score, duration_ms) pairs.

    Weighting by duration keeps a 500ms mechanical stage from counting for as
    much as the two-minute competitive analysis. Before any stage has been
    timed every weight is zero, so that case falls back to a plain mean rather
    than dividing by nothing.
    """
    if not scored:
        return None
    total_weight = sum(weight for _, weight in scored)
    if total_weight <= 0:
        return round(sum(score for score, _ in scored) / len(scored), 1)
    return round(sum(score * weight for score, weight in scored) / total_weight, 1)


def _scored(stage_rows: list[dict[str, Any]], field: str) -> list[tuple[float, float]]:
    return [
        (float(row[field]), float(row.get("duration_ms") or 0))
        for row in stage_rows
        if row.get(field) is not None and row.get("status") == StageStatus.SUCCEEDED
    ]


def overall_quality(stage_rows: list[dict[str, Any]]) -> float | None:
    """Duration-weighted mean completeness, or None if nothing is scored yet."""
    return _weighted_mean(_scored(stage_rows, "completeness"))


def overall_confidence(stage_rows: list[dict[str, Any]]) -> float | None:
    """The reviewer's view, shown alongside completeness.

    Completeness only asks whether the shape of the answer is right; the critic
    asks whether it is about this business. Reporting one without the other
    reads as a verdict on the whole job when it is not.
    """
    return _weighted_mean(_scored(stage_rows, "confidence"))
