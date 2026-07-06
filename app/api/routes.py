import json
import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.models.bi import (
    BIJobStatus,
    BISubmitRequest,
    BISubmitResponse,
    BriefInput,
    BriefResponse,
    MarketResearchResult,
    MarketResearchResultResponse,
    MarketResearchStatusResponse,
    MarketResearchSubmitRequest,
    MarketResearchSubmitResponse,
)
from app.orchestrator.bi_pipeline import BIPipeline
from app.orchestrator.market_research import MarketResearchService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["bi"])

_bi_pipeline = BIPipeline()
_market_research = MarketResearchService()


@router.post("/bi/submit", response_model=BISubmitResponse)
async def bi_submit(body: BISubmitRequest) -> BISubmitResponse:
    job = await _bi_pipeline.submit(url=body.url, email=body.email)
    return BISubmitResponse(
        job_id=job["job_id"],
        status="processing",
        message="Analysis started. Report will be emailed upon completion.",
    )


@router.get("/bi/status/{job_id}", response_model=BIJobStatus)
async def bi_status(job_id: str) -> BIJobStatus:
    job = await _bi_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    events_raw = job.get("events", "[]")
    try:
        events = json.loads(events_raw) if isinstance(events_raw, str) else events_raw
    except (json.JSONDecodeError, TypeError):
        events = []
    return BIJobStatus(
        job_id=job["job_id"],
        status=job["status"],
        progress=events,
        error=job.get("error"),
    )


@router.post("/bi/brief/{job_id}", response_model=BriefResponse)
async def create_brief(job_id: str, body: BriefInput) -> BriefResponse:
    job = await _bi_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    data = body.model_dump(exclude={"session_id"})
    row = await _bi_pipeline.create_brief(job_id, body.session_id, data)
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create brief")
    return BriefResponse(**cast(dict[str, Any], row))


@router.get("/bi/brief/{job_id}", response_model=BriefResponse)
async def get_brief(job_id: str) -> BriefResponse:
    row = await _bi_pipeline.get_brief(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Brief not found")
    return BriefResponse(**cast(dict[str, Any], row))


@router.get("/bi/download/{job_id}")
async def bi_download(job_id: str, format: str = Query("pdf", pattern="^(pdf|docx)$")) -> Response:
    job = await _bi_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "complete":
        raise HTTPException(status_code=400, detail="Job not yet complete")

    file_path = Path(job["pdf_path"] or "") if format == "pdf" else Path(job["docx_path"] or "")
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    media_type = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_path.name)


@router.get("/bi/models")
async def bi_models() -> dict[str, str]:
    from app.config import settings as s

    return {
        "Scraper": "N/A (no LLM)",
        "Business Profile": s.agent_model_business_profile,
        "Market Analysis": s.agent_model_market_analysis,
        "Competitive Analysis": s.agent_model_competitive_analysis,
        "SWOT Analysis": s.agent_model_swot,
        "Marketing Analysis": s.agent_model_marketing,
        "Report Generation": s.agent_model_report_writer,
        "Email Delivery": "N/A (no LLM)",
    }


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/market-research/submit", response_model=MarketResearchSubmitResponse)
async def market_research_submit(body: MarketResearchSubmitRequest) -> MarketResearchSubmitResponse:
    job = await _market_research.submit(body.market_query)
    return MarketResearchSubmitResponse(
        job_id=job["job_id"],
        status="processing",
        message="Market research started. Check back later for results.",
    )


@router.get("/market-research/status/{job_id}", response_model=MarketResearchStatusResponse)
async def market_research_status(job_id: str) -> MarketResearchStatusResponse:
    row = await _market_research.get_status(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return MarketResearchStatusResponse(
        job_id=row["job_id"],
        status=row["status"],
        error=row.get("error"),
    )


@router.get("/market-research/result/{job_id}", response_model=MarketResearchResultResponse)
async def market_research_result(job_id: str) -> MarketResearchResultResponse:
    row = await _market_research.get_result(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    result = None
    if row.get("result_json"):
        try:
            data = json.loads(row["result_json"])
            result = MarketResearchResult(**data)
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to parse market research result: %s", e)
    return MarketResearchResultResponse(
        job_id=row["job_id"],
        status=row["status"],
        result=result,
        error=row.get("error"),
    )


@router.get("/market-research/download/{job_id}")
async def market_research_download(
    job_id: str,
    format: str = Query("pdf", pattern="^(pdf|docx)$"),
) -> Response:
    row = await _market_research.get_status(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] != "complete":
        raise HTTPException(status_code=400, detail="Job not yet complete")

    file_path = Path(row["pdf_path"] or "") if format == "pdf" else Path(row["docx_path"] or "")
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    media_type = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_path.name)
