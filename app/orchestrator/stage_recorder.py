"""Records stage transitions for one job.

Writes survive a flaky database: a failed write is buffered and replayed on the
next transition rather than aborting the run. The app talks to a managed
Postgres over the network, and a five-minute analysis must not be lost to a
momentary blip.

Buffered writes replay strictly in order. A stage's finishing UPDATE run ahead
of its starting INSERT matches no row and silently does nothing - and the INSERT
replayed afterwards then leaves the stage showing "running" for ever.

The legacy ``jobs.events`` blob is no longer written. It was a read-modify-write
of one JSON column per transition, racy under concurrency; stage rows replaced
it and the frontend reads those.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.analysis.runner import StageMetrics
from app.orchestrator.job_store import JobStore
from app.pipeline.stages import ErrorKind, StageStatus, stages_for

logger = logging.getLogger(__name__)

MAX_BUFFERED_WRITES = 200


class StageRecorder:
    def __init__(self, store: JobStore, job_id: str, kind: str = "bi") -> None:
        self._store = store
        self._job_id = job_id
        self._kind = kind
        self._seq = {s.slug: s.seq for s in stages_for(kind)}
        self._pending: list[Callable[[], Awaitable[None]]] = []
        self._running: set[str] = set()

    @property
    def running(self) -> frozenset[str]:
        """Stages started and not yet settled."""
        return frozenset(self._running)

    async def _write(self, op: Callable[[], Awaitable[None]]) -> None:
        """Run a write after replaying anything buffered from earlier failures.

        The first failure stops the replay and re-buffers everything from that
        write on, so later writes never overtake an earlier one.
        """
        ops, self._pending = [*self._pending, op], []
        for i, current in enumerate(ops):
            try:
                await current()
            except Exception:  # noqa: BLE001 - never let bookkeeping kill a job
                waiting = [o for o in ops[i:] if o is not _noop]
                self._pending = (waiting + self._pending)[:MAX_BUFFERED_WRITES]
                logger.warning(
                    "Stage write failed for job %s; buffered %d write(s) for retry",
                    self._job_id, len(self._pending), exc_info=True,
                )
                return

    async def flush(self) -> None:
        """Best-effort replay of buffered writes, e.g. at the end of a job."""
        if self._pending:
            await self._write(_noop)

    async def start(self, slug: str) -> None:
        seq = self._seq[slug]
        self._running.add(slug)

        async def op() -> None:
            await self._store.start_stage(self._job_id, self._kind, slug, seq)

        await self._write(op)

    async def succeed(
        self,
        slug: str,
        metrics: StageMetrics | None = None,
        artifact_json: str | None = None,
    ) -> None:
        self._running.discard(slug)

        async def op() -> None:
            await self._store.finish_stage(
                self._job_id,
                slug,
                StageStatus.SUCCEEDED,
                model=(metrics.model or None) if metrics else None,
                prompt_version=(metrics.prompt_version or None) if metrics else None,
                attempts=metrics.attempts if metrics else 1,
                duration_ms=metrics.duration_ms if metrics else None,
                prompt_tokens=metrics.prompt_tokens if metrics else None,
                completion_tokens=metrics.completion_tokens if metrics else None,
                completeness=metrics.completeness if metrics else None,
                confidence=metrics.confidence if metrics else None,
                confidence_before_retry=metrics.confidence_before_retry if metrics else None,
                retry_confidence=metrics.retry_confidence if metrics else None,
                critic_note=metrics.critic_note if metrics else None,
                warnings=metrics.warnings if metrics else None,
                artifact_json=artifact_json,
            )

        await self._write(op)

    async def fail(
        self,
        slug: str,
        error: str,
        error_kind: str = ErrorKind.INTERNAL,
        metrics: StageMetrics | None = None,
        *,
        duration_ms: int | None = None,
        attempts: int | None = None,
    ) -> None:
        """Record a failed stage, keeping whatever it managed to measure.

        A stage that dies after three attempts has still spent time, tokens and
        a model. Dropping those on the failure path would lose exactly the
        numbers needed to diagnose it. Quality scores are deliberately not
        written: the contract never ran, so there is nothing to report.

        Mechanical stages have no ``StageMetrics``, so they pass their duration
        and attempt count directly; those win over ``metrics`` when both given.
        """
        self._running.discard(slug)

        async def op() -> None:
            await self._store.finish_stage(
                self._job_id,
                slug,
                StageStatus.FAILED,
                model=(metrics.model or None) if metrics else None,
                prompt_version=(metrics.prompt_version or None) if metrics else None,
                attempts=(
                    attempts
                    if attempts is not None
                    else (metrics.attempts if metrics else 0)
                ),
                duration_ms=(
                    duration_ms
                    if duration_ms is not None
                    else (metrics.duration_ms if metrics else None)
                ),
                prompt_tokens=metrics.prompt_tokens if metrics else None,
                completion_tokens=metrics.completion_tokens if metrics else None,
                error=error,
                error_kind=error_kind,
            )

        await self._write(op)

    async def fail_running(self, error: str, error_kind: str = ErrorKind.INTERNAL) -> None:
        """Settle every stage still marked running as failed.

        For the job boundary's catch-all: an exception that is not a StageError
        names no stage, and without this the stage it escaped from kept
        spinning in the UI after the job itself had been marked failed.
        """
        for slug in sorted(self._running, key=lambda s: self._seq.get(s, 0)):
            await self.fail(slug, error, error_kind)

    async def skip(self, slug: str, reason: str = "") -> None:
        self._running.discard(slug)

        async def op() -> None:
            await self._store.finish_stage(
                self._job_id, slug, StageStatus.SKIPPED, error=reason or None
            )

        await self._write(op)

    async def record_metrics_only(self, slug: str, **kwargs: Any) -> None:
        """Mark a non-agent stage succeeded with hand-supplied numbers."""
        self._running.discard(slug)

        async def op() -> None:
            await self._store.finish_stage(
                self._job_id, slug, StageStatus.SUCCEEDED, **kwargs
            )

        await self._write(op)


async def _noop() -> None:
    return None
