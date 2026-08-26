"""Market research: macro analysis, sizing, resource compilation.

Previously one call asked a single model for PESTEL, Porter's Five Forces,
TAM/SAM/SOM, a SWOT and six categories of resources in one response - and
reported no progress at all while it worked. It now runs as three conditioned
stages, each recorded like any BI stage, so the page shows real progress and
each step is scored on its own.
"""

import asyncio
import logging
import time
import uuid
from typing import Any

from app.analysis.critic import build_critic
from app.analysis.runner import AgentRunner, StageContext, StageError
from app.analysis.specs import MACRO_SPEC, RESOURCES_SPEC, SIZING_SPEC
from app.config import settings
from app.models.bi import MarketResearchResult, MarketResources, MarketSynthesis
from app.orchestrator.job_store import JobStore
from app.orchestrator.stage_recorder import StageRecorder
from app.pipeline.stages import JobStatus
from app.report.generator import MarketResearchReportGenerator

logger = logging.getLogger(__name__)


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
        job_id = uuid.uuid4().hex[:12]
        job_data = await self._store.create_market_research_job(job_id, market_query)
        from app.orchestrator.runner import QueueFullError, enqueue

        try:
            await enqueue(job_id, self.run(job_id, market_query))
        except QueueFullError:
            await self._store.delete_market_research_job(job_id)
            raise
        return job_data

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

    async def run(self, job_id: str, market_query: str) -> None:
        """Execute one research job. Never raises - failure is recorded."""
        await self._store.init()
        recorder = StageRecorder(self._store, job_id, kind="market_research")
        ctx = StageContext(market_query=market_query)

        try:
            await self._store.update_market_research_status(job_id, JobStatus.RUNNING)
            logger.info("Market research started for: %s", market_query)

            await recorder.start(MACRO_SPEC.key)
            macro = await self._runner.run_with_review(MACRO_SPEC, ctx)
            ctx.macro = macro.value
            await recorder.succeed(
                MACRO_SPEC.key, macro.metrics, artifact_json=macro.value.model_dump_json()
            )

            await recorder.start(SIZING_SPEC.key)
            sizing = await self._runner.run_with_review(SIZING_SPEC, ctx)
            ctx.sizing = sizing.value
            await recorder.succeed(
                SIZING_SPEC.key, sizing.metrics, artifact_json=sizing.value.model_dump_json()
            )

            await recorder.start(RESOURCES_SPEC.key)
            resources = await self._runner.run_with_review(RESOURCES_SPEC, ctx)
            await recorder.succeed(
                RESOURCES_SPEC.key,
                resources.metrics,
                artifact_json=resources.value.model_dump_json(),
            )

            result = assemble_result(market_query, sizing.value, resources.value)

            await recorder.start("render")
            render_started = time.monotonic()
            pdf_path, docx_path = await self._render(result)
            # Timed like every other stage, so the row shows a duration rather
            # than the em-dash that made a finished render look unstarted.
            await recorder.record_metrics_only(
                "render",
                duration_ms=int((time.monotonic() - render_started) * 1000),
                attempts=1,
            )

            await self._store.complete_market_research(
                job_id, result.model_dump_json(), pdf_path=pdf_path, docx_path=docx_path
            )
            logger.info("Market research completed for job %s", job_id)

        except StageError as e:
            logger.warning("Market research %s failed at %s: %s", job_id, e.stage, e.message)
            await recorder.fail(e.stage, e.message, e.error_kind, e.metrics)
            await self._store.update_market_research_status(job_id, JobStatus.FAILED, e.message)
        except Exception as e:  # noqa: BLE001 - the job boundary catches everything
            logger.exception("Market research failed for job %s", job_id)
            await self._store.update_market_research_status(job_id, JobStatus.FAILED, str(e))
        finally:
            await recorder.flush()

    async def _render(self, result: MarketResearchResult) -> tuple[str | None, str | None]:
        """Render documents. A rendering failure must not lose the analysis."""
        try:
            pdf_path = str(await asyncio.to_thread(self._report_gen.generate_pdf, result))
            docx_path = str(await asyncio.to_thread(self._report_gen.generate_docx, result))
            return pdf_path, docx_path
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to generate market research documents: %s", e)
            return None, None


def assemble_result(
    market_query: str, sizing: MarketSynthesis, resources: MarketResources
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
    )
