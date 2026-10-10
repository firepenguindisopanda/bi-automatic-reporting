"""Market research: macro analysis, sizing, resource compilation.

Previously one call asked a single model for PESTEL, Porter's Five Forces,
TAM/SAM/SOM, a SWOT and six categories of resources in one response - and
reported no progress at all while it worked. It now runs as three conditioned
stages, each recorded like any BI stage, so the page shows real progress and
each step is scored on its own.

Everything the stages produce reaches the reader: the macro view and the
market SWOT used to be scored, reviewed and then dropped on assembly.

A rendering failure does not lose the analysis. The job ends `partial` - the
result is on the page, the documents are not - instead of `completed` with
download buttons that 404.
"""

import asyncio
import logging
import time
import uuid
from typing import Any

from pydantic import BaseModel, ValidationError

from app.analysis.critic import build_critic
from app.analysis.runner import AgentRunner, AgentSpec, StageContext, StageError, ms_since
from app.analysis.specs import MACRO_SPEC, RESOURCES_SPEC, SIZING_SPEC
from app.config import settings
from app.models.bi import (
    MarketMacroAnalysis,
    MarketResearchResult,
    MarketResources,
    MarketSynthesis,
)
from app.orchestrator.bi_pipeline import RETRYABLE_STATUSES, JobStateError
from app.orchestrator.job_store import JobStore
from app.orchestrator.stage_recorder import StageRecorder
from app.pipeline.stages import ErrorKind, JobStatus
from app.report.generator import MarketResearchReportGenerator

logger = logging.getLogger(__name__)

# A strict chain: each stage reads the one before it.
STAGE_ORDER: tuple[tuple[AgentSpec[Any], type[BaseModel]], ...] = (
    (MACRO_SPEC, MarketMacroAnalysis),
    (SIZING_SPEC, MarketSynthesis),
    (RESOURCES_SPEC, MarketResources),
)


class MarketResearchService:
    def __init__(self, runner: AgentRunner | None = None) -> None:
        self._runner = runner or AgentRunner(
            critic=build_critic(),
            critic_retry_threshold=settings.critic_retry_threshold,
        )
        self._store = JobStore()
        self._report_gen = MarketResearchReportGenerator(output_dir=settings.report_output_dir)

    async def submit(self, market_query: str) -> dict[str, str]:
        await self._store.init()
        job_id = uuid.uuid4().hex
        job_data = await self._store.create_market_research_job(job_id, market_query)
        from app.orchestrator.runner import QueueFullError, enqueue

        try:
            await enqueue(job_id, self.run(job_id, market_query))
        except QueueFullError:
            await self._store.delete_market_research_job(job_id)
            raise
        return job_data

    async def retry(self, job_id: str) -> dict[str, str]:
        """Resume a failed or interrupted job from its first unfinished stage."""
        await self._store.init()
        row = await self._store.get_market_research_job(job_id)
        if row is None:
            raise JobStateError("Job not found", 404)
        status = row["status"]
        if status not in RETRYABLE_STATUSES:
            raise JobStateError(f"Only a failed or interrupted job can be retried; this one is {status}")
        if int(row.get("retries") or 0) >= settings.max_retries_per_job:
            raise JobStateError(
                f"This job has already been retried {settings.max_retries_per_job} times", 429
            )
        await self._store.requeue_market_research_for_retry(job_id)
        from app.orchestrator.runner import QueueFullError, enqueue

        try:
            await enqueue(job_id, self.run(job_id, row["market_query"], resume=True))
        except QueueFullError:
            await self._store.update_market_research_status(job_id, status, row.get("error"))
            raise
        return {"job_id": job_id, "status": JobStatus.QUEUED}

    async def get_status(self, job_id: str) -> dict[str, Any] | None:
        await self._store.init()
        return await self._store.get_market_research_job(job_id)

    async def get_result(self, job_id: str) -> dict[str, Any] | None:
        await self._store.init()
        row = await self._store.get_market_research_job(job_id)
        if row:
            row["result_json"] = row.get("result_json") or None
        return row

    async def get_stages(self, job_id: str) -> list[dict[str, Any]]:
        await self._store.init()
        return await self._store.get_stages(job_id)

    async def run(self, job_id: str, market_query: str, *, resume: bool = False) -> None:
        """Execute one research job. Never raises - failure is recorded."""
        await self._store.init()
        recorder = StageRecorder(self._store, job_id, kind="market_research")
        ctx = StageContext(market_query=market_query)
        outputs: dict[str, BaseModel] = {}
        terminal: tuple[JobStatus, str | None] | None = None

        try:
            await self._store.update_market_research_status(job_id, JobStatus.RUNNING)
            logger.info("Market research started for job %s", job_id)
            if resume:
                outputs = await self._restore(job_id)

            for spec, _model in STAGE_ORDER:
                if spec.key in outputs:
                    _apply(ctx, spec.key, outputs[spec.key])
                    continue
                await recorder.start(spec.key)
                result = await self._runner.run_with_review(spec, ctx)
                outputs[spec.key] = result.value
                _apply(ctx, spec.key, result.value)
                await recorder.succeed(
                    spec.key, result.metrics, artifact_json=result.value.model_dump_json()
                )

            result_model = assemble_result(
                market_query,
                _as(outputs["sizing"], MarketSynthesis),
                _as(outputs["resources"], MarketResources),
                _as(outputs["macro"], MarketMacroAnalysis),
            )

            await recorder.start("render")
            render_started = time.monotonic()
            pdf_path, docx_path, render_error = await self._render(result_model)
            if render_error is None:
                await recorder.record_metrics_only(
                    "render", duration_ms=ms_since(render_started), attempts=1
                )
                terminal = (JobStatus.COMPLETED, None)
            else:
                await recorder.fail(
                    "render", render_error, ErrorKind.INTERNAL,
                    duration_ms=ms_since(render_started), attempts=1,
                )
                terminal = (
                    JobStatus.PARTIAL,
                    f"The analysis is complete but the documents could not be rendered: {render_error}",
                )

            # The result lands while the job still reads running; the terminal
            # status follows in `finally`, after the stage writes are flushed.
            await self._store.complete_market_research(
                job_id,
                result_model.model_dump_json(),
                pdf_path=pdf_path,
                docx_path=docx_path,
                status=JobStatus.RUNNING,
            )
            logger.info("Market research finished for job %s (%s)", job_id, terminal[0])

        except StageError as e:
            logger.warning("Market research %s failed at %s: %s", job_id, e.stage, e.message)
            await recorder.fail(e.stage, e.message, e.error_kind, e.metrics)
            terminal = (JobStatus.FAILED, e.message)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the job boundary catches everything
            logger.exception("Market research failed for job %s", job_id)
            message = str(e) or type(e).__name__
            await recorder.fail_running(message, ErrorKind.INTERNAL)
            terminal = (JobStatus.FAILED, message)
        finally:
            await recorder.flush()
            if terminal is not None:
                await self._store.update_market_research_status(job_id, *terminal)

    async def _restore(self, job_id: str) -> dict[str, BaseModel]:
        """Stored outputs a retry can reuse: the unbroken prefix of the chain."""
        rows = {row["stage"]: row for row in await self._store.get_stages(job_id)}
        outputs: dict[str, BaseModel] = {}
        for spec, model in STAGE_ORDER:
            row = rows.get(spec.key)
            if row is None or row.get("status") != "succeeded" or not row.get("artifact_json"):
                break
            try:
                outputs[spec.key] = model.model_validate_json(row["artifact_json"])
            except ValidationError:
                break
        return outputs

    async def _render(
        self, result: MarketResearchResult
    ) -> tuple[str | None, str | None, str | None]:
        """Render documents. Returns (pdf, docx, error); never raises."""
        try:
            pdf_path = str(await asyncio.to_thread(self._report_gen.generate_pdf, result))
            docx_path = str(await asyncio.to_thread(self._report_gen.generate_docx, result))
            return pdf_path, docx_path, None
        except Exception as e:  # noqa: BLE001 - the analysis must survive a render failure
            logger.exception("Failed to generate market research documents")
            return None, None, str(e) or type(e).__name__


def _apply(ctx: StageContext, key: str, value: BaseModel) -> None:
    """Thread a stage's output into the context the next stage reads."""
    if key == "macro":
        ctx.macro = value
    elif key == "sizing":
        ctx.sizing = value


def _as[T: BaseModel](value: BaseModel, model: type[T]) -> T:
    if not isinstance(value, model):
        raise TypeError(f"expected {model.__name__}, got {type(value).__name__}")
    return value


def assemble_result(
    market_query: str,
    sizing: MarketSynthesis,
    resources: MarketResources,
    macro: MarketMacroAnalysis | None = None,
) -> MarketResearchResult:
    """Fold the staged outputs back into the single result the UI expects."""
    return MarketResearchResult(
        market_query=market_query,
        executive_summary=sizing.executive_summary,
        key_insights=sizing.key_insights,
        recommendations=sizing.recommendations,
        risks_and_assumptions=sizing.risks_and_assumptions,
        summary=sizing.summary,
        market_size=sizing.market_size,
        growth_rate=sizing.growth_rate,
        key_players=resources.key_players,
        news_sources=resources.news_sources,
        industry_websites=resources.industry_websites,
        competitor_sites=resources.competitor_sites,
        research_papers=resources.research_papers,
        relevant_communities=resources.relevant_communities,
        pestel=macro.pestel if macro is not None else None,
        five_forces=macro.five_forces if macro is not None else None,
        swot=sizing.swot,
    )
