import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.analysis.agents import (
    BIReportWriterAgent,
    BusinessProfileAgent,
    CompetitiveAnalysisAgent,
    MarketAnalysisAgent,
    MarketingAnalysisAgent,
    SWOTAgent,
)
from app.config import settings
from app.email.service import EmailService
from app.llm.client import LLMClient
from app.models.bi import AnalysisArtifact
from app.orchestrator.job_store import JobStore
from app.report.generator import ReportGenerator
from app.scraper.engine import ScraperEngine

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=2)


class BIPipeline:
    def __init__(self) -> None:
        self._llm = LLMClient()
        self._scraper = ScraperEngine()
        self._profile_agent = BusinessProfileAgent(self._llm)
        self._market_agent = MarketAnalysisAgent(self._llm)
        self._competitive_agent = CompetitiveAnalysisAgent(self._llm)
        self._swot_agent = SWOTAgent(self._llm)
        self._marketing_agent = MarketingAnalysisAgent(self._llm)
        self._report_writer = BIReportWriterAgent(self._llm)
        self._report_gen = ReportGenerator(output_dir=settings.report_output_dir)
        self._email_service = EmailService()
        self._store = JobStore()

    async def init(self) -> None:
        await self._store.init()

    async def submit(self, url: str, email: str) -> dict[str, str]:
        await self._store.init()
        job_id = uuid.uuid4().hex[:12]
        job_data = await self._store.create_job(job_id, url, email)
        asyncio.create_task(self._run_async(job_id, url, email))
        return job_data

    async def get_job(self, job_id: str) -> dict[str, str] | None:
        await self._store.init()
        return await self._store.get_job(job_id)

    async def create_brief(self, job_id: str, session_id: str, data: dict[str, str]) -> dict[str, object]:
        await self._store.init()
        return await self._store.create_brief(job_id, session_id, data)

    async def get_brief(self, job_id: str) -> dict[str, object] | None:
        await self._store.init()
        return await self._store.get_brief(job_id)

    async def delete_brief(self, job_id: str) -> bool:
        await self._store.init()
        return await self._store.delete_brief(job_id)

    def _run_sync(self, job_id: str, url: str, email: str) -> None:
        """Synchronous pipeline runner - runs in a thread pool.

        All LLM client + agent objects are created fresh inside this thread
        so that ChatNVIDIA's httpx client lives in the same thread context.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_thlocal(job_id, url, email))
        except Exception:
            logger.exception("Pipeline failed for job %s", job_id)
        finally:
            loop.close()

    async def _run_thlocal(self, job_id: str, url: str, email: str) -> None:
        try:
            llm = LLMClient()
            agents: dict[str, Any] = {
                "profile": BusinessProfileAgent(llm),
                "market": MarketAnalysisAgent(llm),
                "competitive": CompetitiveAnalysisAgent(llm),
                "swot": SWOTAgent(llm),
                "marketing": MarketingAnalysisAgent(llm),
                "writer": BIReportWriterAgent(llm),
            }
            report_gen = ReportGenerator(output_dir=settings.report_output_dir)
            email_svc = EmailService()

            await self._store.init()
            await self._store.update_status(job_id, "scraping")
            await self._store.add_event(job_id, "step_start", "Scraper")
            scraped = await self._scraper.scrape(url)
            await self._store.add_event(job_id, "step_complete", "Scraper")

            if scraped.error:
                await self._store.update_status(job_id, "error", f"Scraping failed: {scraped.error}")
                return

            await self._store.update_status(job_id, "analyzing")
            artifact = AnalysisArtifact(url=url, scraped=scraped)

            await self._store.add_event(job_id, "step_start", "Business Profile")
            profile = agents["profile"].analyze(scraped)
            if profile is None:
                raise RuntimeError("Business Profile agent returned None")
            artifact.business_profile = profile
            await self._store.add_event(job_id, "step_complete", "Business Profile")

            await self._store.add_event(job_id, "step_start", "Market Analysis")
            market = agents["market"].analyze(profile, scraped)
            if market is None:
                raise RuntimeError("Market Analysis agent returned None")
            artifact.market_analysis = market
            await self._store.add_event(job_id, "step_complete", "Market Analysis")

            await self._store.add_event(job_id, "step_start", "Competitive Analysis")
            competitive = agents["competitive"].analyze(profile, market, scraped)
            if competitive is None:
                raise RuntimeError("Competitive Analysis agent returned None")
            artifact.competitive_analysis = competitive
            await self._store.add_event(job_id, "step_complete", "Competitive Analysis")

            await self._store.add_event(job_id, "step_start", "SWOT Analysis")
            swot = agents["swot"].analyze(profile, market, competitive)
            if swot is None:
                raise RuntimeError("SWOT agent returned None")
            artifact.swot_analysis = swot
            await self._store.add_event(job_id, "step_complete", "SWOT Analysis")

            await self._store.add_event(job_id, "step_start", "Marketing Analysis")
            marketing = agents["marketing"].analyze(profile, scraped)
            if marketing is None:
                raise RuntimeError("Marketing Analysis agent returned None")
            artifact.marketing = marketing
            await self._store.add_event(job_id, "step_complete", "Marketing Analysis")

            await self._store.add_event(job_id, "step_start", "Report Generation")
            report = agents["writer"].write(profile, market, competitive, swot)
            if report is None:
                raise RuntimeError("Report Writer agent returned None")
            artifact.report = report

            pdf_path = report_gen.generate_pdf(report, profile, profile.company_name, marketing)
            docx_path = report_gen.generate_docx(report, profile, profile.company_name, marketing)
            await self._store.add_event(job_id, "step_complete", "Report Generation")

            await self._store.complete_job(
                job_id,
                artifact_json=artifact.model_dump_json(),
                pdf_path=str(pdf_path),
                docx_path=str(docx_path),
            )

            await self._store.add_event(job_id, "sending_email", "Email Delivery")
            email_ok = await email_svc.send_report(
                to_email=email,
                company_name=profile.company_name,
                pdf_path=pdf_path,
                docx_path=docx_path,
            )
            if email_ok:
                await self._store.add_event(job_id, "email_sent", "Email Delivery")
                logger.info("Report emailed to %s for job %s", email, job_id)
            else:
                logger.warning("Email delivery failed for job %s", job_id)

        except Exception as e:
            logger.exception("BI pipeline failed for job %s", job_id)
            await self._store.update_status(job_id, "error", str(e))

    async def _run_async(self, job_id: str, url: str, email: str) -> None:
        """Fully async pipeline runner - runs in the event loop via create_task."""
        try:
            await self._store.init()
            await self._store.update_status(job_id, "scraping")
            await self._store.add_event(job_id, "step_start", "Scraper")
            scraped = await self._scraper.scrape(url)
            await self._store.add_event(job_id, "step_complete", "Scraper")

            if scraped.error:
                await self._store.update_status(job_id, "error", f"Scraping failed: {scraped.error}")
                return

            await self._store.update_status(job_id, "analyzing")
            artifact = AnalysisArtifact(url=url, scraped=scraped)

            await self._store.add_event(job_id, "step_start", "Business Profile")
            profile = await self._profile_agent.analyze_async(scraped)
            if profile is None:
                raise RuntimeError("Business Profile agent returned None")
            artifact.business_profile = profile
            await self._store.add_event(job_id, "step_complete", "Business Profile")

            await self._store.add_event(job_id, "step_start", "Market Analysis")
            market = await self._market_agent.analyze_async(profile, scraped)
            if market is None:
                raise RuntimeError("Market Analysis agent returned None")
            artifact.market_analysis = market
            await self._store.add_event(job_id, "step_complete", "Market Analysis")

            await self._store.add_event(job_id, "step_start", "Competitive Analysis")
            competitive = await self._competitive_agent.analyze_async(profile, market, scraped)
            if competitive is None:
                raise RuntimeError("Competitive Analysis agent returned None")
            artifact.competitive_analysis = competitive
            await self._store.add_event(job_id, "step_complete", "Competitive Analysis")

            await self._store.add_event(job_id, "step_start", "SWOT Analysis")
            swot = await self._swot_agent.analyze_async(profile, market, competitive)
            if swot is None:
                raise RuntimeError("SWOT agent returned None")
            artifact.swot_analysis = swot
            await self._store.add_event(job_id, "step_complete", "SWOT Analysis")

            await self._store.add_event(job_id, "step_start", "Marketing Analysis")
            marketing = await self._marketing_agent.analyze_async(profile, scraped)
            if marketing is None:
                raise RuntimeError("Marketing Analysis agent returned None")
            artifact.marketing = marketing
            await self._store.add_event(job_id, "step_complete", "Marketing Analysis")

            await self._store.add_event(job_id, "step_start", "Report Generation")
            report = await self._report_writer.write_async(profile, market, competitive, swot)
            if report is None:
                raise RuntimeError("Report Writer agent returned None")
            artifact.report = report

            pdf_path = await asyncio.to_thread(
                self._report_gen.generate_pdf, report, profile, profile.company_name, marketing
            )
            docx_path = await asyncio.to_thread(
                self._report_gen.generate_docx, report, profile, profile.company_name, marketing
            )
            await self._store.add_event(job_id, "step_complete", "Report Generation")

            await self._store.complete_job(
                job_id,
                artifact_json=artifact.model_dump_json(),
                pdf_path=str(pdf_path),
                docx_path=str(docx_path),
            )

            await self._store.add_event(job_id, "sending_email", "Email Delivery")
            email_ok = await self._email_service.send_report(
                to_email=email,
                company_name=profile.company_name,
                pdf_path=pdf_path,
                docx_path=docx_path,
            )
            if email_ok:
                await self._store.add_event(job_id, "email_sent", "Email Delivery")
                logger.info("Report emailed to %s for job %s", email, job_id)
            else:
                logger.warning("Email delivery failed for job %s", job_id)

        except Exception as e:
            logger.exception("BI pipeline failed for job %s", job_id)
            await self._store.update_status(job_id, "error", str(e))
