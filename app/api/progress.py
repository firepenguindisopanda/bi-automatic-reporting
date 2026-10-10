"""Assembling job progress, and streaming it.

Polling and SSE serve the same payload, so a client that loses the stream falls
back without losing any detail, and a page refresh reconstructs the full state
from a single request.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.models.bi import JobProgress, StageReport
from app.pipeline.eta import (
    elapsed_ms,
    estimate_remaining_ms,
    overall_confidence,
    overall_quality,
)
from app.pipeline.stages import TERMINAL_JOB_STATUSES, StageStatus, stages_for

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0
"""Used only when no change notifier is supplied."""
FALLBACK_REREAD_SECONDS = 5.0
"""With a notifier, re-read anyway this often - a write from outside this
process sends no notification, and must still show up eventually."""
HEARTBEAT_SECONDS = 15.0
MAX_STREAM_SECONDS = 3600.0

WaitForChange = Callable[[float], Awaitable[None]]


def _parse_warnings(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(w) for w in raw]
    try:
        parsed = json.loads(raw)
        return [str(w) for w in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def build_stage_reports(kind: str, rows: list[dict[str, Any]]) -> list[StageReport]:
    """Every stage in the pipeline, including ones that have not started.

    The client gets the whole shape up front rather than watching rows appear,
    so the timeline does not jump around as the job progresses.
    """
    by_slug = {row["stage"]: row for row in rows}
    reports: list[StageReport] = []
    for stage in stages_for(kind):
        row = by_slug.get(stage.slug)
        if row is None:
            reports.append(
                StageReport(
                    stage=stage.slug,
                    display=stage.display,
                    label=stage.label,
                    seq=stage.seq,
                    status=StageStatus.PENDING,
                )
            )
            continue
        reports.append(
            StageReport(
                stage=stage.slug,
                display=stage.display,
                label=stage.label,
                seq=stage.seq,
                status=str(row.get("status") or StageStatus.PENDING),
                duration_ms=row.get("duration_ms"),
                model=row.get("model"),
                attempts=int(row.get("attempts") or 0),
                prompt_tokens=row.get("prompt_tokens"),
                completion_tokens=row.get("completion_tokens"),
                completeness=row.get("completeness"),
                confidence=row.get("confidence"),
                critic_note=row.get("critic_note"),
                warnings=_parse_warnings(row.get("warnings")),
                error=row.get("error"),
                error_kind=row.get("error_kind"),
            )
        )
    return reports


async def build_progress(
    store: Any,
    job_id: str,
    kind: str,
    status: str,
    error: str | None = None,
    subject: str | None = None,
) -> JobProgress:
    rows = await store.get_stages(job_id)
    from app.orchestrator.runner import queue_position

    # A finished job has no remaining time. Estimating one regardless left
    # failed and interrupted jobs advertising "about a minute left" forever.
    eta = (
        None
        if status in TERMINAL_JOB_STATUSES
        else await estimate_remaining_ms(store, kind, rows)
    )

    return JobProgress(
        job_id=job_id,
        kind=kind,
        status=status,
        error=error,
        subject=subject,
        stages=build_stage_reports(kind, rows),
        elapsed_ms=elapsed_ms(rows),
        eta_ms=eta,
        quality=overall_quality(rows),
        confidence=overall_confidence(rows),
        queue_position=queue_position(job_id),
    )


def _sse(event: str, payload: str) -> str:
    return f"event: {event}\ndata: {payload}\n\n"


async def stream_progress(
    load: Callable[[], Awaitable[JobProgress | None]],
    wait_for_change: WaitForChange | None = None,
) -> AsyncIterator[str]:
    """Server-sent events for one job, until it reaches a terminal state.

    Each event is the full progress snapshot - the same payload polling
    returns - sent whenever it changes. A snapshot rather than per-transition
    deltas means a client that misses an event, or reconnects, is never left
    holding a stale view.

    With ``wait_for_change`` the stream sleeps until the job is written to
    (or ``FALLBACK_REREAD_SECONDS`` passes); without it, it polls every
    second. A comment heartbeat keeps idle connections alive through proxy read
    timeouts. nginx additionally needs `proxy_buffering off`, or the stream is
    buffered and the UI looks frozen.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_payload: str | None = None
    last_sent = started

    try:
        while loop.time() - started < MAX_STREAM_SECONDS:
            progress = await load()
            if progress is None:
                yield _sse("error", json.dumps({"detail": "Job not found"}))
                return

            payload = progress.model_dump_json()
            if payload != last_payload:
                yield _sse("progress", payload)
                last_payload = payload
                last_sent = loop.time()

            if progress.status in TERMINAL_JOB_STATUSES:
                yield _sse("done", json.dumps({"status": progress.status}))
                return

            if wait_for_change is not None:
                await wait_for_change(min(FALLBACK_REREAD_SECONDS, HEARTBEAT_SECONDS))
            else:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
            if loop.time() - last_sent >= HEARTBEAT_SECONDS:
                yield ": keepalive\n\n"
                last_sent = loop.time()
    except asyncio.CancelledError:
        # The client went away. Nothing to clean up; just stop.
        raise
    except Exception:  # noqa: BLE001 - a stream error must not surface as a 500 mid-body
        logger.exception("Progress stream failed")
        yield _sse("error", json.dumps({"detail": "Progress stream failed"}))
