"""The BI pipeline: scrape, analyse, render, deliver.

Every stage is recorded with its duration, model, attempt count and quality
scores, so the frontend can show what happened rather than just a spinner.

Marketing runs alongside the market -> competitive -> SWOT chain. It reads only
the profile and the page, so waiting for the chain cost a whole stage of wall
time for nothing.

The report the reader gets is assembled here from the stage outputs that were
scored and reviewed. The report writer contributes only the executive summary,
recommendations and risks; it used to rewrite every section, and that unscored
rewrite was what the PDF showed.

The job reaches a terminal state only after delivery is attempted - previously
it was marked complete before the email was sent, which made delivery
impossible to observe. Stage writes are flushed before that terminal status, so
a client that stops listening at the terminal status has seen every stage.

A failed or interrupted job can be retried. Stages whose stored output is still
valid - they succeeded, and everything they read was reused too - are loaded
instead of run again, so a retry costs only what is left.
"""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.analysis.critic import build_critic
from app.analysis.runner import (
    AgentRunner,
    AgentSpec,
    StageContext,
    StageError,
    StageMetrics,
    ms_since,
)
from app.analysis.specs import (
    COMPETITIVE_SPEC,
    MARKET_SPEC,
    MARKETING_SPEC,
    PROFILE_SPEC,
    REPORT_SPEC,
    SWOT_SPEC,
)
from app.config import settings
from app.email.policy import DAY_HOURS, DeliveryRefusedError, check_recipient_allowed
from app.email.service import EmailService
from app.email.templates import ReportDigest
from app.models.bi import (
    AnalysisArtifact,
    BIReport,
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketingAnalysis,
    ReportSynthesis,
    ScrapedContent,
    SWOTAnalysis,
    build_report,
)
from app.orchestrator.job_store import JobStore
from app.orchestrator.stage_recorder import StageRecorder
from app.pipeline.eta import overall_quality
from app.pipeline.stages import ErrorKind, JobStatus
from app.report.generator import ReportGenerator
from app.scraper.engine import ScraperEngine

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({JobStatus.FAILED, JobStatus.INTERRUPTED})
RESENDABLE_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.PARTIAL})

# What each analysis stage produces, where it lives on the context, and what it
# reads. A stored result is reused on retry only when every input was reused
# too: re-running the profile changes what the market stage would have said.
STAGE_OUTPUTS: dict[str, type[BaseModel]] = {
    "scrape": ScrapedContent,
    "profile": BusinessProfile,
    "market": MarketAnalysis,
    "competitive": CompetitiveAnalysis,
    "swot": SWOTAnalysis,
    "marketing": MarketingAnalysis,
    "report": ReportSynthesis,
}
CONTEXT_FIELD = {
    "scrape": "scraped",
    "profile": "profile",
    "market": "market",
    "competitive": "competitive",
    "swot": "swot",
    "marketing": "marketing",
    "report": "report",
}
DEPENDS_ON: dict[str, tuple[str, ...]] = {
    "scrape": (),
    "profile": ("scrape",),
    "market": ("profile", "scrape"),
    "competitive": ("profile", "market", "scrape"),
    "swot": ("profile", "market", "competitive"),
    "marketing": ("profile", "scrape"),
    "report": ("profile", "market", "competitive", "swot"),
}


class JobStateError(Exception):
    """An action the job's current state does not allow. Carries an HTTP status."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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
        # Jobs with a resend in flight. One process runs every job, so a set is
        # enough to stop a double click from sending the report twice.
        self._resending: set[str] = set()

    async def init(self) -> None:
        await self._store.init()

    async def close(self) -> None:
        await self._store.close()

    # --- Submission ----------------------------------------------------------

    async def submit(self, url: str, email: str) -> dict[str, str]:
        """Queue a job. Raises DeliveryRefusedError when the recipient is not allowed."""
        await self._store.init()
        await self._check_recipient(email)
        # The id doubles as the only credential for a job's report, progress
        # and resend, so it carries the full 122 random bits of a UUID4.
        job_id = uuid.uuid4().hex
        job_data = await self._store.create_job(job_id, url, email)
        from app.orchestrator.runner import QueueFullError, enqueue

        try:
            # The row is written first so a worker can never start ahead of it.
            await enqueue(job_id, self.run(job_id, url, email))
        except QueueFullError:
            await self._store.delete_job(job_id)
            raise
        return job_data

    async def _check_recipient(self, email: str) -> None:
        check_recipient_allowed(email)
        recent = await self._store.count_jobs_for_recipient(email, DAY_HOURS)
        cap = settings.max_reports_per_recipient_per_day
        if recent >= cap:
            raise DeliveryRefusedError(
                f"That address has already been sent {cap} reports in the last 24 hours. "
                "Try again tomorrow."
            )

    async def retry(self, job_id: str) -> dict[str, str]:
        """Resume a failed or interrupted job from its first unfinished stage."""
        await self._store.init()
        job = await self._store.get_job(job_id)
        if job is None:
            raise JobStateError("Job not found", 404)
        status = job["status"]
        if status not in RETRYABLE_STATUSES:
            raise JobStateError(f"Only a failed or interrupted job can be retried; this one is {status}")
        if int(job.get("retries") or 0) >= settings.max_retries_per_job:
            raise JobStateError(
                f"This job has already been retried {settings.max_retries_per_job} times", 429
            )
        url, email = job["url"], job["email"]
        await self._store.requeue_for_retry(job_id)
        from app.orchestrator.runner import QueueFullError, enqueue

        try:
            await enqueue(job_id, self.run(job_id, url, email, resume=True))
        except QueueFullError:
            # Leave it as it was, so the retry button is still there to press.
            await self._store.update_status(job_id, status, job.get("error"))
            raise
        return {"job_id": job_id, "status": JobStatus.QUEUED}

    # --- Delivery ------------------------------------------------------------

    async def resend(self, job_id: str) -> tuple[bool, str]:
        """Send a finished job's report again. Raises DeliveryRefusedError or JobStateError."""
        await self._store.init()
        job = await self._store.get_job(job_id)
        if job is None:
            raise JobStateError("Job not found", 404)
        if job["status"] not in RESENDABLE_STATUSES:
            raise JobStateError("Only a finished report can be sent again")
        pdf_path = Path(job.get("pdf_path") or "")
        if not job.get("pdf_path") or not pdf_path.exists():
            raise JobStateError("The report file is no longer available", 410)
        if job_id in self._resending:
            raise JobStateError("This report is already being sent")

        sent_for_job = len(await self._store.get_deliveries(job_id))
        if sent_for_job >= settings.max_deliveries_per_job:
            raise DeliveryRefusedError(
                f"This report has already been sent {sent_for_job} times, the most allowed."
            )
        recent = await self._store.count_deliveries_to_recipient(job["email"], DAY_HOURS)
        if recent >= settings.max_reports_per_recipient_per_day:
            raise DeliveryRefusedError(
                "That address has reached its limit of reports for the last 24 hours. "
                "Try again tomorrow."
            )

        self._resending.add(job_id)
        try:
            return await self._resend(job, pdf_path)
        finally:
            self._resending.discard(job_id)

    async def _resend(self, job: dict[str, Any], pdf_path: Path) -> tuple[bool, str]:
        job_id = job["job_id"]
        docx_raw = job.get("docx_path") or ""
        docx_path = Path(docx_raw) if docx_raw and Path(docx_raw).exists() else None
        digest = _digest_from_artifact(
            job.get("artifact_json"), score=await self._score(job_id), report_url=_report_url(job_id)
        )
        was_partial = job["status"] == JobStatus.PARTIAL

        recorder = StageRecorder(self._store, job_id, kind="bi")
        if was_partial:
            await recorder.start("deliver")
        started = time.monotonic()
        result = await self._email_service.send_report(
            to_email=job["email"], digest=digest, pdf_path=pdf_path, docx_path=docx_path
        )
        duration = ms_since(started)
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
            if was_partial:
                await recorder.record_metrics_only(
                    "deliver", duration_ms=duration, attempts=result.attempts
                )
                await recorder.flush()
                await self._store.update_status(job_id, JobStatus.COMPLETED)
            return True, "Sent"

        error = result.error or "unknown error"
        # A completed job was delivered once already. A failed re-send is
        # recorded as a delivery attempt but must not rewrite that history:
        # it used to flip a delivered job to `partial`.
        if was_partial:
            await recorder.fail(
                "deliver", error, ErrorKind.UPSTREAM_ERROR,
                duration_ms=duration, attempts=result.attempts,
            )
            await recorder.flush()
            await self._store.update_status(job_id, JobStatus.PARTIAL, error)
        return False, error

    async def email_health(self) -> dict[str, Any]:
        """Whether the configured email provider is usable. Sends nothing."""
        return await self._email_service.verify()

    async def recover_interrupted(self) -> int:
        """Settle jobs a previous process left unfinished. Returns how many."""
        await self._store.init()
        return await self._store.mark_interrupted_jobs()

    # --- Reads ---------------------------------------------------------------

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
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

    # --- The run -------------------------------------------------------------

    async def run(self, job_id: str, url: str, email: str, *, resume: bool = False) -> None:
        """Execute one job end to end. Never raises - failure is recorded."""
        await self._store.init()
        recorder = StageRecorder(self._store, job_id, kind="bi")
        ctx = StageContext(url=url)
        terminal: tuple[JobStatus, str | None] | None = None

        try:
            await self._store.update_status(job_id, JobStatus.RUNNING)
            reused = await self._restore(job_id, ctx) if resume else set()
            if reused:
                logger.info("Job %s resuming; reusing %s", job_id, ", ".join(sorted(reused)))

            if "scrape" not in reused:
                await self._scrape(recorder, url, ctx)
            await self._analyse(recorder, ctx, reused)
            report = build_report(
                ctx.require_report(),
                ctx.require_profile(),
                ctx.require_market(),
                ctx.require_competitive(),
                ctx.require_swot(),
            )
            score = await self._score(job_id)
            pdf_path, docx_path = await self._render(recorder, ctx, report, score)

            artifact = AnalysisArtifact(
                url=url,
                scraped=ctx.scraped,
                business_profile=ctx.profile,
                market_analysis=ctx.market,
                competitive_analysis=ctx.competitive,
                swot_analysis=ctx.swot,
                marketing=ctx.marketing,
                report=report,
            )
            await self._store.complete_job(
                job_id,
                artifact_json=artifact.model_dump_json(),
                pdf_path=str(pdf_path),
                docx_path=str(docx_path),
                status=JobStatus.RUNNING,
            )
            digest = ReportDigest.from_report(
                report, ctx.require_profile(), ctx.marketing,
                source_url=url, score=score, report_url=_report_url(job_id),
            )
            terminal = await self._deliver(job_id, email, recorder, digest, pdf_path, docx_path)

        except StageError as e:
            logger.warning("Job %s failed at stage %s: %s", job_id, e.stage, e.message)
            await recorder.fail(e.stage, e.message, e.error_kind, e.metrics)
            terminal = (JobStatus.FAILED, e.message)
        except asyncio.CancelledError:
            # Shutdown. Leave the job running so startup recovery marks it
            # interrupted, and from there it can be retried.
            raise
        except Exception as e:  # noqa: BLE001 - the job boundary catches everything
            logger.exception("Job %s failed", job_id)
            message = str(e) or type(e).__name__
            await recorder.fail_running(message, ErrorKind.INTERNAL)
            terminal = (JobStatus.FAILED, message)
        finally:
            await recorder.flush()
            if terminal is not None:
                await self._store.update_status(job_id, *terminal)

    async def _restore(self, job_id: str, ctx: StageContext) -> set[str]:
        """Load every stored stage output that a retry can safely reuse."""
        rows = {row["stage"]: row for row in await self._store.get_stages(job_id)}
        reused: set[str] = set()
        for slug, model in STAGE_OUTPUTS.items():  # dependency order
            row = rows.get(slug)
            if row is None or row.get("status") != "succeeded" or not row.get("artifact_json"):
                continue
            if any(dep not in reused for dep in DEPENDS_ON[slug]):
                continue
            try:
                value = model.model_validate_json(row["artifact_json"])
            except ValidationError:
                logger.warning("Job %s: stored %s output no longer validates; re-running", job_id, slug)
                continue
            setattr(ctx, CONTEXT_FIELD[slug], value)
            reused.add(slug)
        return reused

    async def _scrape(self, recorder: StageRecorder, url: str, ctx: StageContext) -> None:
        await recorder.start("scrape")
        started = time.monotonic()
        scraped = await self._scraper.scrape(url)
        duration = ms_since(started)

        if scraped.error:
            # A refused URL is a policy decision, not an upstream fault.
            kind = ErrorKind.BLOCKED if "Refusing to fetch" in scraped.error else ErrorKind.HTTP_ERROR
            raise StageError(
                "scrape",
                kind,
                f"Scraping failed: {scraped.error}",
                metrics=StageMetrics(model="", prompt_version="", attempts=1, duration_ms=duration),
            )

        ctx.scraped = scraped
        await recorder.record_metrics_only(
            "scrape",
            duration_ms=duration,
            attempts=1,
            completeness=_scrape_completeness(scraped),
            # Stored so a retry can resume without fetching the page again -
            # and without the page having changed under the stages that read it.
            artifact_json=scraped.model_dump_json(),
        )

    async def _analyse(self, recorder: StageRecorder, ctx: StageContext, reused: set[str]) -> None:
        """Run the six agent stages, threading each result into the context."""
        await self._run_stage(recorder, PROFILE_SPEC, ctx, reused)

        # Both branches run to their own end even if the other fails, so
        # whatever finished is stored and a retry can pick it up.
        outcomes = await asyncio.gather(
            self._run_chain(recorder, (MARKET_SPEC, COMPETITIVE_SPEC, SWOT_SPEC), ctx, reused),
            self._run_stage(recorder, MARKETING_SPEC, ctx, reused),
            return_exceptions=True,
        )
        errors = [o for o in outcomes if isinstance(o, BaseException)]
        if errors:
            # The first failure becomes the job's; any other is recorded on its
            # own stage here, since only one exception can leave this method.
            for extra in errors[1:]:
                if isinstance(extra, StageError):
                    await recorder.fail(extra.stage, extra.message, extra.error_kind, extra.metrics)
            raise errors[0]

        await self._run_stage(recorder, REPORT_SPEC, ctx, reused)

    async def _run_chain(
        self,
        recorder: StageRecorder,
        specs: tuple[AgentSpec[Any], ...],
        ctx: StageContext,
        reused: set[str],
    ) -> None:
        for spec in specs:
            await self._run_stage(recorder, spec, ctx, reused)

    async def _run_stage(
        self, recorder: StageRecorder, spec: AgentSpec[Any], ctx: StageContext, reused: set[str]
    ) -> None:
        if spec.key in reused:
            return
        await recorder.start(spec.key)
        result = await self._runner.run_with_review(spec, ctx)
        setattr(ctx, CONTEXT_FIELD[spec.key], result.value)
        await recorder.succeed(spec.key, result.metrics, artifact_json=result.value.model_dump_json())
        if result.metrics.warnings:
            logger.info(
                "Stage %s scored %.0f with %d warning(s): %s",
                spec.key,
                result.metrics.completeness,
                len(result.metrics.warnings),
                "; ".join(result.metrics.warnings[:3]),
            )

    async def _score(self, job_id: str) -> float | None:
        """The job's quality score as the page shows it, for the cover and the email.

        Cosmetic there, so a failed read leaves it off rather than failing the job.
        """
        try:
            return overall_quality(await self._store.get_stages(job_id))
        except Exception:  # noqa: BLE001
            logger.warning("Job %s: could not read stage scores", job_id, exc_info=True)
            return None

    async def _render(
        self, recorder: StageRecorder, ctx: StageContext, report: BIReport, score: float | None
    ) -> tuple[Path, Path]:
        await recorder.start("render")
        started = time.monotonic()
        profile = ctx.require_profile()
        try:
            # CPU-bound, and the heaviest step on a small host; keep it off the loop.
            pdf_path = await asyncio.to_thread(
                self._report_gen.generate_pdf, report, profile, profile.company_name, ctx.marketing,
                score=score, source_url=ctx.url,
            )
            docx_path = await asyncio.to_thread(
                self._report_gen.generate_docx, report, profile, profile.company_name, ctx.marketing
            )
        except Exception as e:  # noqa: BLE001 - named for the user, not swallowed
            logger.exception("Rendering failed")
            raise StageError(
                "render",
                ErrorKind.INTERNAL,
                f"Rendering failed: {e}",
                metrics=StageMetrics(
                    model="", prompt_version="", attempts=1, duration_ms=ms_since(started)
                ),
            ) from e
        await recorder.record_metrics_only("render", duration_ms=ms_since(started), attempts=1)
        return Path(pdf_path), Path(docx_path)

    async def _deliver(
        self,
        job_id: str,
        email: str,
        recorder: StageRecorder,
        digest: ReportDigest,
        pdf_path: Path,
        docx_path: Path,
    ) -> tuple[JobStatus, str | None]:
        """Attempt delivery and return the job's terminal status.

        A failed send yields `partial`: the report exists and is downloadable,
        so the run is not a failure - it just did not reach the inbox.
        """
        await recorder.start("deliver")
        started = time.monotonic()
        result = await self._email_service.send_report(
            to_email=email, digest=digest, pdf_path=pdf_path, docx_path=docx_path
        )
        duration = ms_since(started)

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
            logger.info("Report emailed for job %s via %s", job_id, result.provider)
            return JobStatus.COMPLETED, None

        error = result.error or "unknown error"
        await recorder.fail(
            "deliver", error, ErrorKind.UPSTREAM_ERROR, duration_ms=duration, attempts=result.attempts
        )
        logger.warning("Job %s produced a report but delivery failed: %s", job_id, error)
        return JobStatus.PARTIAL, error


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


def _report_url(job_id: str) -> str | None:
    """The job page, for the email's link. None unless PUBLIC_BASE_URL is set."""
    base = settings.public_base_url.strip().rstrip("/")
    return f"{base}/jobs/{job_id}" if base else None


def _digest_from_artifact(
    artifact_json: str | None, *, score: float | None, report_url: str | None
) -> ReportDigest:
    """The email's view of a stored report, for a re-send.

    An artifact that no longer validates still gets a message: just the
    company name, which is all the old template ever showed.
    """
    try:
        artifact = AnalysisArtifact.model_validate_json(artifact_json or "")
    except ValidationError:
        artifact = None
    if artifact is not None and artifact.report is not None and artifact.business_profile is not None:
        return ReportDigest.from_report(
            artifact.report, artifact.business_profile, artifact.marketing,
            source_url=artifact.url, score=score, report_url=report_url,
        )
    return ReportDigest(
        company_name=_company_name_from_artifact(artifact_json), score=score, report_url=report_url
    )


def _company_name_from_artifact(artifact_json: str | None) -> str:
    """Company name for the email subject, recovered from the stored artifact."""
    if not artifact_json:
        return "your business"
    try:
        data = json.loads(artifact_json)
        return str(data.get("business_profile", {}).get("company_name") or "your business")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return "your business"
