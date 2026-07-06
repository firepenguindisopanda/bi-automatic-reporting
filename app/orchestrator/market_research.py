import asyncio
import logging
import uuid

from app.analysis.agents import MarketResearchAgent
from app.config import settings
from app.llm.client import LLMClient
from app.models.bi import MarketResearchResult
from app.orchestrator.job_store import JobStore
from app.report.generator import MarketResearchReportGenerator

logger = logging.getLogger(__name__)


class MarketResearchService:
    def __init__(self) -> None:
        self._llm = LLMClient()
        self._agent = MarketResearchAgent(self._llm)
        self._store = JobStore()
        self._report_gen = MarketResearchReportGenerator(output_dir=settings.report_output_dir)

    async def submit(self, market_query: str) -> dict[str, str]:
        await self._store.init()
        job_id = uuid.uuid4().hex[:12]
        job_data = await self._store.create_market_research_job(job_id, market_query)
        asyncio.create_task(self._run(job_id, market_query))
        return job_data

    async def get_status(self, job_id: str) -> dict[str, str | None] | None:
        await self._store.init()
        return await self._store.get_market_research_job(job_id)

    async def get_result(self, job_id: str) -> dict[str, str | None] | None:
        await self._store.init()
        row = await self._store.get_market_research_job(job_id)
        if row:
            row["result_json"] = row.get("result_json") or None
        return row

    async def _run(self, job_id: str, market_query: str) -> None:
        try:
            await self._store.init()
            await self._store.update_market_research_status(job_id, "processing")
            logger.info("Market research started for: %s", market_query)

            result = await self._agent.research(market_query)
            if result is None:
                raise RuntimeError("Market research agent returned None")

            pdf_path = docx_path = None
            try:
                pdf_path = str(
                    await asyncio.to_thread(self._report_gen.generate_pdf, result)
                )
                docx_path = str(
                    await asyncio.to_thread(self._report_gen.generate_docx, result)
                )
            except Exception as e:
                logger.warning("Failed to generate documents for job %s: %s", job_id, e)

            await self._store.complete_market_research(
                job_id, result.model_dump_json(),
                pdf_path=pdf_path, docx_path=docx_path,
            )
            logger.info("Market research completed for job %s", job_id)

        except Exception as e:
            logger.exception("Market research failed for job %s", job_id)
            await self._store.update_market_research_status(job_id, "error", str(e))
