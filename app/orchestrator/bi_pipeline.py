"""The BI pipeline: scrape, analyse, render, deliver.

Stages run in sequence through a single AgentRunner. Each transition is
recorded with its duration, model, attempt count and quality scores, so the
frontend can show what happened rather than just a spinner.

The job reaches a terminal state only after delivery is attempted. Previously
it was marked complete before the email was sent, which made the delivery stage
impossible to observe.
"""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from app.analysis.critic import build_critic
from app.analysis.runner import AgentRunner, StageContext, StageError
from app.analysis.specs import (
    COMPETITIVE_SPEC,
    MARKET_SPEC,
    MARKETING_SPEC,
    PROFILE_SPEC,
    REPORT_SPEC,
    SWOT_SPEC,
)
from app.config import settings
from app.email.service import EmailService
from app.models.bi import AnalysisArtifact
from app.orchestrator.job_store import JobStore
from app.orchestrator.stage_recorder import StageRecorder
from app.pipeline.stages import ErrorKind, JobStatus
from app.report.generator import ReportGenerator
from app.scraper.engine import ScraperEngine

logger = logging.getLogger(__name__)


class BIPipeline:
    def __init__(self, runner: AgentRunner | None = None) -> None:
        self._runner = runner or AgentRunner(
            critic=build_critic(),
            critic_retry_threshold=settings.critic_retry_threshold,
        )
        self._scraper = ScraperEngine()
        self._report_gen = ReportGenerator(output_dir=settings.report_output_dir)
        self._email_service = EmailService()
        self._store = JobStore()

    async def init(self) -> None:
        await self._store.init()

    async def close(self) -> None:
        await self._store.close()

    async def resend(self, job_id: str) -> tuple[bool, str]:
        """Retry delivery for a job whose report exists but never arrived."""
        await self._store.init()
        job = await self._store.get_job(job_id)
        if job is None:
            return False, "Job not found"
        pdf_path = Path(job.get("pdf_path") or "")
        if not pdf_path.exists():
            return False, "The report file is no longer on disk"

        docx_raw = job.get("docx_path") or ""
        docx_path = Path(docx_raw) if docx_raw and Path(docx_raw).exists() else None
        company = _company_name_from_artifact(job.get("artifact_json"))
        recorder = StageRecorder(self._store, job_id, kind="bi")
        await recorder.start("deliver")
        started = time.monotonic()
        result = await self._email_service.send_report(
            to_email=job["email"],
            company_name=company,
            pdf_path=pdf_path,
            docx_path=docx_path,
        )
        duration = int((time.monotonic() - started) * 1000)
        await self._store.record_delivery(
            job_id,
            provider=result.provider,
            to_email=job["email"],
            status="sent" if result.ok else "failed",
            attempts=result.attempts,
            message_id=result.message_id,
            last_error=result.error,
        )
        if result.ok:
            await recorder.record_metrics_only(
                "deliver", duration_ms=duration, attempts=result.attempts
            )
            await self._store.update_status(job_id, JobStatus.COMPLETED)
            return True, "Sent"
        await recorder.fail(
            "deliver",
            result.error or "unknown error",
            ErrorKind.UPSTREAM_ERROR,
            duration_ms=duration,
            attempts=result.attempts,
        )
        await self._store.update_status(job_id, JobStatus.PARTIAL, result.error)
        return False, result.error or "unknown error"

    async def email_health(self) -> dict[str, Any]:
        """Whether the configured email provider is usable. Sends nothing."""
        return await self._email_service.verify()

    async def recover_interrupted(self) -> int:
        """Settle jobs left running by a crash. Returns how many were affected."""
        await self._store.init()
        return await self._store.mark_interrupted_jobs()

    async def submit(self, url: str, email: str) -> dict[str, str]:
        await self._store.init()
        job_id = uuid.uuid4().hex[:12]
        job_data = await self._store.create_job(job_id, url, email)
        from app.orchestrator.runner import enqueue

        await enqueue(job_id, self.run(job_id, url, email))
        return job_data

    async def get_job(self, job_id: str) -> dict[str, str] | None:
        await self._store.init()
        return await self._store.get_job(job_id)

    async def get_stages(self, job_id: str) -> list[dict[str, Any]]:
        await self._store.init()
        return await self._store.get_stages(job_id)

    async def create_brief(self, job_id: str, session_id: str, data: dict[str, str]) -> dict[str, object]:
        await self._store.init()
        return await self._store.create_brief(job_id, session_id, data)

    async def get_brief(self, job_id: str) -> dict[str, object] | None:
        await self._store.init()
        return await self._store.get_brief(job_id)

    async def delete_brief(self, job_id: str) -> bool:
        await self._store.init()
        return await self._store.delete_brief(job_id)

    async def run(self, job_id: str, url: str, email: str) -> None:
        """Execute one job end to end. Never raises - failure is recorded."""
        await self._store.init()
        recorder = StageRecorder(self._store, job_id, kind="bi")
        artifact = AnalysisArtifact(url=url)
        ctx = StageContext(url=url)

        try:
            await self._store.update_status(job_id, JobStatus.RUNNING)

            if not await self._scrape(job_id, url, recorder, artifact, ctx):
                return
            await self._analyse(recorder, artifact, ctx)
            pdf_path, docx_path = await self._render(recorder, ctx)

            await self._store.complete_job(
                job_id,
                artifact_json=artifact.model_dump_json(),
                pdf_path=str(pdf_path),
                docx_path=str(docx_path),
                status=JobStatus.RUNNING,
            )
            await self._deliver(job_id, email, recorder, ctx, pdf_path, docx_path)

        except StageError as e:
            logger.warning("Job %s failed at stage %s: %s", job_id, e.stage, e.message)
            await recorder.fail(e.stage, e.message, e.error_kind, e.metrics)
            await self._store.update_status(job_id, JobStatus.FAILED, e.message)
        except Exception as e:  # noqa: BLE001 - the job boundary catches everything
            logger.exception("Job %s failed", job_id)
            await self._store.update_status(job_id, JobStatus.FAILED, str(e))
        finally:
            await recorder.flush()

    async def _scrape(
        self,
        job_id: str,
        url: str,
        recorder: StageRecorder,
        artifact: AnalysisArtifact,
        ctx: StageContext,
    ) -> bool:
        await recorder.start("scrape")
        started = time.monotonic()
        scraped = await self._scraper.scrape(url)
        duration = int((time.monotonic() - started) * 1000)

        if scraped.error:
            # A refused URL is a policy decision, not an upstream fault.
            kind = (
                ErrorKind.BLOCKED
                if "Refusing to fetch" in scraped.error
                else ErrorKind.HTTP_ERROR
            )
            await recorder.fail(
                "scrape", scraped.error, kind, duration_ms=duration, attempts=1
            )
            await self._store.update_status(
                job_id, JobStatus.FAILED, f"Scraping failed: {scraped.error}"
            )
            return False

        artifact.scraped = scraped
        ctx.scraped = scraped
        await recorder.record_metrics_only(
            "scrape", duration_ms=duration, attempts=1, completeness=_scrape_completeness(scraped)
        )
        return True

    async def _analyse(
        self, recorder: StageRecorder, artifact: AnalysisArtifact, ctx: StageContext
    ) -> None:
        """Run the six agent stages, threading each result into the context."""
        plan = (
            (PROFILE_SPEC, "profile", "business_profile"),
            (MARKET_SPEC, "market", "market_analysis"),
            (COMPETITIVE_SPEC, "competitive", "competitive_analysis"),
            (SWOT_SPEC, "swot", "swot_analysis"),
            (MARKETING_SPEC, "marketing", "marketing"),
            (REPORT_SPEC, "report", "report"),
        )
        for spec, ctx_field, artifact_field in plan:
            await recorder.start(spec.key)
            result = await self._runner.run_with_review(spec, ctx)
            setattr(ctx, ctx_field, result.value)
            setattr(artifact, artifact_field, result.value)
            await recorder.succeed(
                spec.key, result.metrics, artifact_json=result.value.model_dump_json()
            )
            if result.metrics.warnings:
                logger.info(
                    "Stage %s scored %.0f with %d warning(s): %s",
                    spec.key,
                    result.metrics.completeness,
                    len(result.metrics.warnings),
                    "; ".join(result.metrics.warnings[:3]),
                )

    async def _render(self, recorder: StageRecorder, ctx: StageContext) -> tuple[Any, Any]:
        await recorder.start("render")
        started = time.monotonic()
        profile = ctx.require_profile()
        report = ctx.report_or_raise()
        # WeasyPrint is CPU-bound and the heaviest step on a Pi; keep it off the loop.
        pdf_path = await asyncio.to_thread(
            self._report_gen.generate_pdf, report, profile, profile.company_name, ctx.marketing
        )
        docx_path = await asyncio.to_thread(
            self._report_gen.generate_docx, report, profile, profile.company_name, ctx.marketing
        )
        await recorder.record_metrics_only(
            "render", duration_ms=int((time.monotonic() - started) * 1000), attempts=1
        )
        return pdf_path, docx_path

    async def _deliver(
        self,
        job_id: str,
        email: str,
        recorder: StageRecorder,
        ctx: StageContext,
        pdf_path: Any,
        docx_path: Any,
    ) -> None:
        """Attempt delivery, then settle the job's terminal status.

        A failed send yields `partial`: the report exists and is downloadable,
        so the run is not a failure - it just did not reach the inbox.
        """
        await recorder.start("deliver")
        started = time.monotonic()
        profile = ctx.require_profile()
        result = await self._email_service.send_report(
            to_email=email,
            company_name=profile.company_name,
            pdf_path=pdf_path,
            docx_path=docx_path,
        )
        duration = int((time.monotonic() - started) * 1000)

        await self._store.record_delivery(
            job_id,
            provider=result.provider,
            to_email=email,
            status="sent" if result.ok else "failed",
            attempts=result.attempts,
            message_id=result.message_id,
            last_error=result.error,
        )

        if result.ok:
            await recorder.record_metrics_only(
                "deliver", duration_ms=duration, attempts=result.attempts
            )
            await self._store.update_status(job_id, JobStatus.COMPLETED)
            logger.info("Report emailed to %s for job %s via %s", email, job_id, result.provider)
        else:
            await recorder.fail(
                "deliver",
                result.error or "unknown error",
                ErrorKind.UPSTREAM_ERROR,
                duration_ms=duration,
                attempts=result.attempts,
            )
            await self._store.update_status(job_id, JobStatus.PARTIAL, result.error)
            logger.warning("Job %s completed but delivery failed: %s", job_id, result.error)


def _scrape_completeness(scraped: Any) -> float:
    """How much usable material the page actually yielded.

    A page that returns 200 with almost no text produces a weak analysis; this
    surfaces that at the first stage instead of leaving it unexplained later.
    """
    score = 0.0
    if len(getattr(scraped, "text_content", "") or "") >= 500:
        score += 50
    elif len(getattr(scraped, "text_content", "") or "") >= 100:
        score += 25
    if getattr(scraped, "title", ""):
        score += 20
    if getattr(scraped, "description", ""):
        score += 15
    if getattr(scraped, "headings", []):
        score += 15
    return min(100.0, score)


def _company_name_from_artifact(artifact_json: str | None) -> str:
    """Company name for the email subject, recovered from the stored artifact."""
    if not artifact_json:
        return "your business"
    try:
        data = json.loads(artifact_json)
        return str(data.get("business_profile", {}).get("company_name") or "your business")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return "your business"
