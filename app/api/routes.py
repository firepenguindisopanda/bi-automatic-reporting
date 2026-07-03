import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.models.bi import BIJobStatus, BISubmitRequest, BISubmitResponse, BriefInput, BriefResponse
from app.orchestrator.bi_pipeline import BIPipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["bi"])

_bi_pipeline = BIPipeline()


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
    return BIJobStatus(
        job_id=job["job_id"],
        status=job["status"],
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


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
