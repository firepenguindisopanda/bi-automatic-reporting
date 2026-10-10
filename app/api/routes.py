import json
import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.analysis.specs import BI_SPECS
from app.api.progress import build_progress, stream_progress
from app.config import settings
from app.models.bi import (
    BISubmitRequest,
    BISubmitResponse,
    BriefInput,
    BriefResponse,
    JobProgress,
    MarketResearchResult,
    MarketResearchResultResponse,
    MarketResearchStatusResponse,
    MarketResearchSubmitRequest,
    MarketResearchSubmitResponse,
    ResendResponse,
    RetryResponse,
    StageDescriptor,
)
from app.orchestrator.bi_pipeline import BIPipeline, JobStateError
from app.orchestrator.market_research import MarketResearchService
from app.orchestrator.runner import QueueFullError
from app.orchestrator.runner import stats as queue_stats
from app.pipeline.events import job_events as change_events
from app.pipeline.stages import JobStatus, stages_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["bi"])

QUEUE_FULL_RETRY_AFTER = "60"
"""Seconds to advise a client to wait when the backlog is full."""


def _queue_full(exc: QueueFullError) -> HTTPException:
    """Back-pressure is a 503, not a 500.

    The queue refusing work is the design working. Letting the exception
    escape turned it into an unhandled error, which tells the caller the
    service is broken rather than busy, and gives them nothing to retry on.
    """
    return HTTPException(
        status_code=503, detail=str(exc), headers={"Retry-After": QUEUE_FULL_RETRY_AFTER}
    )

def _refused(exc: JobStateError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


_bi_pipeline = BIPipeline()
_market_research = MarketResearchService()


@router.post("/bi/submit", response_model=BISubmitResponse)
async def bi_submit(body: BISubmitRequest) -> BISubmitResponse:
    try:
        job = await _bi_pipeline.submit(url=body.url, email=body.email)
    except QueueFullError as e:
        raise _queue_full(e) from None
    return BISubmitResponse(
        job_id=job["job_id"],
        status="processing",
        message="Analysis started. Report will be emailed upon completion.",
    )


@router.get("/bi/status/{job_id}", response_model=JobProgress)
async def bi_status(job_id: str) -> JobProgress:
    """The full stage array, including stages that have not started.

    Kept as an alias of the progress endpoint for older clients. It used to
    return the legacy events blob, which is no longer written.
    """
    return await bi_progress(job_id)


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
    # `partial` means the report exists but delivery failed - the file is still
    # the user's to download, and refusing it here would be the wrong answer.
    if job["status"] not in (JobStatus.COMPLETED, JobStatus.PARTIAL):
        raise HTTPException(status_code=400, detail="Job not yet complete")

    raw = job["pdf_path"] if format == "pdf" else job["docx_path"]
    file_path = Path(raw) if raw else None
    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    media_type = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_path.name)


@router.get("/bi/models")
async def bi_models() -> dict[str, str]:
    """The model behind each stage, derived from the registry and the specs.

    This was a second hand-written stage list, which is exactly the copy the
    stage registry exists to remove.
    """
    models = {spec.key: spec.model for spec in BI_SPECS}
    return {s.display: models.get(s.slug, "N/A (no LLM)") for s in stages_for("bi")}


@router.get("/bi/progress/{job_id}", response_model=JobProgress)
async def bi_progress(job_id: str) -> JobProgress:
    """Full stage detail. A refresh reconstructs the whole view from this."""
    job = await _bi_pipeline.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return await build_progress(
        _bi_pipeline._store, job_id, "bi", job["status"], job.get("error"),
        subject=job.get("url"),
    )


@router.get("/market-research/progress/{job_id}", response_model=JobProgress)
async def market_research_progress(job_id: str) -> JobProgress:
    row = await _market_research.get_status(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return await build_progress(
        _market_research._store, job_id, "market_research", row["status"], row.get("error"),
        subject=row.get("market_query"),
    )


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str, kind: str = Query("bi", pattern="^(bi|market_research)$")
) -> StreamingResponse:
    """Server-sent progress stream. Clients fall back to polling on failure."""

    seen = change_events.version(job_id)

    async def wait_for_change(timeout: float) -> None:
        nonlocal seen
        seen = await change_events.wait(job_id, seen, timeout)

    async def load() -> JobProgress | None:
        if kind == "bi":
            job = await _bi_pipeline.get_job(job_id)
            if job is None:
                return None
            return await build_progress(
                _bi_pipeline._store, job_id, kind, job["status"], job.get("error"),
                subject=job.get("url"),
            )
        row = await _market_research.get_status(job_id)
        if row is None:
            return None
        return await build_progress(
            _market_research._store, job_id, kind, row["status"], row.get("error"),
            subject=row.get("market_query"),
        )

    return StreamingResponse(
        stream_progress(load, wait_for_change),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Belt and braces alongside `proxy_buffering off` in the nginx block.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/bi/{job_id}/resend", response_model=ResendResponse)
async def bi_resend(job_id: str) -> ResendResponse:
    """Send a finished report again. Limited per job and per recipient."""
    try:
        ok, detail = await _bi_pipeline.resend(job_id)
    except JobStateError as e:
        raise _refused(e) from None
    return ResendResponse(job_id=job_id, sent=ok, detail=detail)


@router.post("/bi/{job_id}/retry", response_model=RetryResponse)
async def bi_retry(job_id: str) -> RetryResponse:
    """Resume a failed or interrupted job from its first unfinished stage."""
    try:
        job = await _bi_pipeline.retry(job_id)
    except QueueFullError as e:
        raise _queue_full(e) from None
    except JobStateError as e:
        raise _refused(e) from None
    return RetryResponse(job_id=job["job_id"], status=job["status"])


@router.post("/market-research/{job_id}/retry", response_model=RetryResponse)
async def market_research_retry(job_id: str) -> RetryResponse:
    """Resume a failed or interrupted research job from its first unfinished stage."""
    try:
        job = await _market_research.retry(job_id)
    except QueueFullError as e:
        raise _queue_full(e) from None
    except JobStateError as e:
        raise _refused(e) from None
    return RetryResponse(job_id=job["job_id"], status=job["status"])


@router.get("/pipelines/{kind}/stages", response_model=list[StageDescriptor])
async def pipeline_stages(kind: str) -> list[StageDescriptor]:
    """Canonical stage list for a pipeline, so the frontend never hardcodes one."""
    try:
        stages = stages_for(kind)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown pipeline: {kind}") from None
    return [
        StageDescriptor(
            slug=s.slug, display=s.display, label=s.label, seq=s.seq, uses_llm=s.uses_llm
        )
        for s in stages
    ]


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Deliberately cheap - the container healthcheck calls this."""
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(response: Response) -> dict[str, Any]:
    """Readiness. Checks the things that make the service actually usable."""
    checks: dict[str, dict[str, Any]] = {}

    try:
        await _bi_pipeline.get_job("__readiness_probe__")
        checks["database"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        checks["database"] = {"ok": False, "detail": str(e)}

    checks["llm_api_key"] = {"ok": bool(settings.nvidia_api_key)}
    if not settings.nvidia_api_key:
        checks["llm_api_key"]["detail"] = "NVIDIA_API_KEY is not set"

    report_dir = Path(settings.report_output_dir)
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        probe = report_dir / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        checks["report_dir"] = {"ok": True, "path": str(report_dir)}
    except Exception as e:  # noqa: BLE001
        checks["report_dir"] = {"ok": False, "path": str(report_dir), "detail": str(e)}

    try:
        health = await _bi_pipeline.email_health()
        checks["email"] = health
    except Exception as e:  # noqa: BLE001
        checks["email"] = {"ok": False, "detail": str(e)}

    checks["job_queue"] = {"ok": True, **queue_stats()}

    ready = all(c.get("ok") for c in checks.values())
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not_ready", "checks": checks}


@router.post("/market-research/submit", response_model=MarketResearchSubmitResponse)
async def market_research_submit(body: MarketResearchSubmitRequest) -> MarketResearchSubmitResponse:
    try:
        job = await _market_research.submit(body.market_query)
    except QueueFullError as e:
        raise _queue_full(e) from None
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
        except Exception as e:
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
    if row["status"] not in (JobStatus.COMPLETED, JobStatus.PARTIAL):
        raise HTTPException(status_code=400, detail="Job not yet complete")

    raw = row["pdf_path"] if format == "pdf" else row["docx_path"]
    if not raw:
        # `partial`: the analysis finished but the documents did not render.
        raise HTTPException(status_code=404, detail="No document was produced for this job")
    file_path = Path(raw)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    media_type = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path=str(file_path), media_type=media_type, filename=file_path.name)
