"""Tests for progress assembly, SSE streaming, ETA and readiness."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.progress import build_progress, build_stage_reports, stream_progress
from app.main import app
from app.models.bi import JobProgress
from app.pipeline.eta import estimate_remaining_ms, overall_confidence, overall_quality
from app.pipeline.stages import BI_STAGES, StageStatus


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestStageReports:
    def test_unstarted_stages_are_reported_as_pending(self) -> None:
        reports = build_stage_reports("bi", [])
        assert len(reports) == len(BI_STAGES)
        assert all(r.status == StageStatus.PENDING for r in reports)

    def test_shape_is_stable_before_any_work(self) -> None:
        """The client renders the whole timeline immediately, not row by row."""
        reports = build_stage_reports("bi", [])
        assert [r.stage for r in reports] == [s.slug for s in BI_STAGES]
        assert reports[0].label == "Scraping the site"

    def test_recorded_stage_carries_its_detail(self) -> None:
        rows = [{
            "stage": "profile", "status": "succeeded", "duration_ms": 8400,
            "model": "meta/llama-3.1-8b-instruct", "attempts": 2,
            "prompt_tokens": 1200, "completion_tokens": 400,
            "completeness": 92.5, "confidence": 88.0, "critic_note": "solid",
            "warnings": json.dumps(["offerings has 2 items, expected at least 3"]),
            "error": None, "error_kind": None,
        }]
        report = {r.stage: r for r in build_stage_reports("bi", rows)}["profile"]
        assert report.status == "succeeded"
        assert report.duration_ms == 8400
        assert report.attempts == 2
        assert report.completeness == 92.5
        assert report.confidence == 88.0
        assert report.warnings == ["offerings has 2 items, expected at least 3"]

    def test_failed_stage_carries_the_reason(self) -> None:
        rows = [{
            "stage": "swot", "status": "failed",
            "error": "no valid SWOTAnalysis object found", "error_kind": "parse_error",
        }]
        report = {r.stage: r for r in build_stage_reports("bi", rows)}["swot"]
        assert report.status == "failed"
        assert report.error_kind == "parse_error"
        assert "SWOTAnalysis" in report.error

    def test_malformed_warnings_do_not_break_rendering(self) -> None:
        rows = [{"stage": "profile", "status": "succeeded", "warnings": "not json"}]
        assert build_stage_reports("bi", rows)[1].warnings == []


class FakeDurationStore:
    def __init__(self, durations: dict[str, list[int]] | None = None) -> None:
        self.durations = durations or {}

    async def recent_stage_durations(
        self, stage: str, model: str, limit: int = 20
    ) -> list[int]:
        return self.durations.get(stage, [])


class TestETA:
    async def test_no_history_gives_no_estimate(self) -> None:
        assert await estimate_remaining_ms(FakeDurationStore(), "bi", []) is None

    async def test_thin_history_gives_no_estimate(self) -> None:
        store = FakeDurationStore({"profile": [5000, 6000]})
        assert await estimate_remaining_ms(store, "bi", []) is None

    async def test_uses_median_per_stage(self) -> None:
        store = FakeDurationStore({s.slug: [1000, 2000, 9000] for s in BI_STAGES})
        eta = await estimate_remaining_ms(store, "bi", [])
        # Median of each stage is 2000; a single 9000 outlier must not dominate.
        assert eta == 2000 * len(BI_STAGES)

    async def test_finished_stages_are_excluded(self) -> None:
        store = FakeDurationStore({s.slug: [1000, 1000, 1000] for s in BI_STAGES})
        rows = [{"stage": "scrape", "status": "succeeded"},
                {"stage": "profile", "status": "succeeded"}]
        eta = await estimate_remaining_ms(store, "bi", rows)
        assert eta == 1000 * (len(BI_STAGES) - 2)

    async def test_stage_without_history_borrows_the_typical_duration(self) -> None:
        store = FakeDurationStore({"profile": [4000, 4000, 4000]})
        eta = await estimate_remaining_ms(store, "bi", [])
        assert eta == 4000 * len(BI_STAGES)

    async def test_everything_finished_is_zero(self) -> None:
        rows = [{"stage": s.slug, "status": "succeeded"} for s in BI_STAGES]
        assert await estimate_remaining_ms(FakeDurationStore(), "bi", rows) == 0

    async def test_lookup_failure_does_not_raise(self) -> None:
        class Broken:
            async def recent_stage_durations(self, *a: Any, **k: Any) -> list[int]:
                raise RuntimeError("db down")

        assert await estimate_remaining_ms(Broken(), "bi", []) is None

    async def test_failed_stages_are_not_counted_as_still_to_come(self) -> None:
        """A stage that broke is not going to run again."""
        store = FakeDurationStore({s.slug: [1000, 1000, 1000] for s in BI_STAGES})
        rows = [{"stage": "scrape", "status": "succeeded"},
                {"stage": "profile", "status": "failed"}]
        eta = await estimate_remaining_ms(store, "bi", rows)
        assert eta == 1000 * (len(BI_STAGES) - 2)


class TestTerminalJobsHaveNoETA:
    """A settled job must not advertise remaining time.

    Failed and interrupted jobs previously kept showing "about a minute left",
    because the estimate was computed regardless of job status and every
    unreached stage still counted towards it.
    """

    class Store:
        async def get_stages(self, job_id: str) -> list[dict[str, Any]]:
            return [{"stage": "scrape", "status": "failed", "duration_ms": 5}]

        async def recent_stage_durations(
            self, stage: str, model: str, limit: int = 20
        ) -> list[int]:
            return [1000, 1000, 1000]

    @pytest.mark.parametrize(
        "status", ["failed", "interrupted", "completed", "partial"]
    )
    async def test_terminal_status_reports_no_eta(self, status: str) -> None:
        progress = await build_progress(self.Store(), "job1", "bi", status)
        assert progress.eta_ms is None

    async def test_running_job_still_gets_an_eta(self) -> None:
        progress = await build_progress(self.Store(), "job1", "bi", "running")
        assert progress.eta_ms is not None and progress.eta_ms > 0


class TestSubject:
    """The payload names the job, so any browser can render the heading.

    The client previously read this from its own localStorage, so a shared
    link or a private window showed a bare job id.
    """

    Store = TestTerminalJobsHaveNoETA.Store

    async def test_subject_is_returned(self) -> None:
        progress = await build_progress(
            self.Store(), "job1", "bi", "running", subject="https://plausible.io"
        )
        assert progress.subject == "https://plausible.io"

    async def test_absent_subject_is_none_not_an_error(self) -> None:
        progress = await build_progress(self.Store(), "job1", "bi", "running")
        assert progress.subject is None


class TestQuality:
    def test_mean_of_scored_stages(self) -> None:
        rows = [
            {"status": "succeeded", "completeness": 100.0},
            {"status": "succeeded", "completeness": 50.0},
        ]
        assert overall_quality(rows) == 75.0

    def test_failed_stages_are_excluded(self) -> None:
        rows = [
            {"status": "succeeded", "completeness": 90.0},
            {"status": "failed", "completeness": 0.0},
        ]
        assert overall_quality(rows) == 90.0

    def test_no_scores_yet(self) -> None:
        assert overall_quality([{"status": "running"}]) is None

    def test_longer_stages_carry_more_weight(self) -> None:
        """A 500ms render must not offset a two-minute analysis."""
        rows = [
            {"status": "succeeded", "completeness": 100.0, "duration_ms": 500},
            {"status": "succeeded", "completeness": 0.0, "duration_ms": 119_500},
        ]
        # Unweighted this would read 50.0.
        assert overall_quality(rows) == 0.4

    def test_untimed_stages_fall_back_to_a_plain_mean(self) -> None:
        rows = [
            {"status": "succeeded", "completeness": 100.0, "duration_ms": None},
            {"status": "succeeded", "completeness": 50.0, "duration_ms": 0},
        ]
        assert overall_quality(rows) == 75.0


class TestConfidence:
    """The reviewer score travels with completeness rather than being dropped."""

    def test_weighted_mean_of_critic_scores(self) -> None:
        rows = [
            {"status": "succeeded", "confidence": 90.0, "duration_ms": 1000},
            {"status": "succeeded", "confidence": 60.0, "duration_ms": 1000},
        ]
        assert overall_confidence(rows) == 75.0

    def test_unreviewed_stages_are_ignored(self) -> None:
        rows = [
            {"status": "succeeded", "confidence": 80.0, "duration_ms": 1000},
            {"status": "succeeded", "confidence": None, "duration_ms": 9000},
        ]
        assert overall_confidence(rows) == 80.0

    def test_none_when_the_critic_is_disabled(self) -> None:
        rows = [{"status": "succeeded", "completeness": 100.0, "confidence": None}]
        assert overall_confidence(rows) is None


class TestStreaming:
    async def _collect(self, progresses: list[JobProgress | None]) -> list[str]:
        calls = {"n": 0}

        async def load() -> JobProgress | None:
            i = min(calls["n"], len(progresses) - 1)
            calls["n"] += 1
            return progresses[i]

        chunks = []
        async for chunk in stream_progress(load):
            chunks.append(chunk)
        return chunks

    async def test_terminal_state_ends_the_stream(self) -> None:
        done = JobProgress(job_id="j", status="completed")
        chunks = await self._collect([done])
        assert any("event: progress" in c for c in chunks)
        assert chunks[-1].startswith("event: done")

    async def test_missing_job_emits_error_and_stops(self) -> None:
        chunks = await self._collect([None])
        assert len(chunks) == 1
        assert "Job not found" in chunks[0]

    async def test_payload_is_the_same_shape_as_polling(self) -> None:
        done = JobProgress(job_id="j", status="completed", quality=91.0)
        chunks = await self._collect([done])
        payload = json.loads(chunks[0].split("data: ", 1)[1].strip())
        assert payload["job_id"] == "j"
        assert payload["quality"] == 91.0

    async def test_unchanged_state_is_not_re_emitted(self) -> None:
        running = JobProgress(job_id="j", status="running")
        done = JobProgress(job_id="j", status="completed")
        chunks = await self._collect([running, running, done])
        progress_events = [c for c in chunks if c.startswith("event: progress")]
        assert len(progress_events) == 2  # running once, then completed


class TestEndpoints:
    def test_progress_404_for_unknown_job(self, client: TestClient) -> None:
        assert client.get("/api/bi/progress/nope").status_code == 404

    def test_sse_sets_no_buffering_headers(self, client: TestClient) -> None:
        """nginx buffers SSE by default, which makes the UI look frozen."""
        with client.stream("GET", "/api/jobs/nope/events") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["x-accel-buffering"] == "no"
            assert "no-cache" in resp.headers["cache-control"]

    def test_sse_rejects_unknown_kind(self, client: TestClient) -> None:
        assert client.get("/api/jobs/x/events?kind=bogus").status_code == 422

    def test_resend_404_for_unknown_job(self, client: TestClient) -> None:
        assert client.post("/api/bi/nope/resend").status_code == 404

    def test_liveness_is_cheap_and_ok(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_readiness_reports_each_check(self, client: TestClient) -> None:
        resp = client.get("/api/health/ready")
        assert resp.status_code in (200, 503)
        checks = resp.json()["checks"]
        assert {"database", "llm_api_key", "report_dir", "email", "job_queue"} <= set(checks)

    def test_readiness_fails_without_an_api_key(self, client: TestClient, monkeypatch) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "nvidia_api_key", "")
        resp = client.get("/api/health/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["llm_api_key"]["ok"] is False
