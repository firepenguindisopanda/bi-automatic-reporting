"""Market research runs as three recorded stages, and never loses its analysis."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.analysis.runner import AgentRunner
from app.config import settings
from app.llm.client import LLMClient
from app.models.bi import MarketResearchResult
from app.orchestrator.market_research import MarketResearchService
from app.pipeline.stages import JobStatus, StageStatus

REPLIES = {
    "MarketMacroAnalysis": json.dumps({
        "pestel": {k: [f"{k} factor"] for k in (
            "political", "economic", "social", "technological", "environmental", "legal")},
        "five_forces": {
            "threat_of_new_entrants": "Moderate - low capital needs",
            "supplier_power": "Low", "buyer_power": "High",
            "threat_of_substitutes": "Moderate", "competitive_rivalry": "High",
        },
    }),
    "MarketSynthesis": json.dumps({
        "executive_summary": "A growing market. " * 10, "summary": "Landscape. " * 40,
        "market_size": "TAM: $10B, SAM: $2B", "growth_rate": "12% CAGR",
        "key_insights": ["i1", "i2", "i3"], "recommendations": ["r1", "r2", "r3"],
        "risks_and_assumptions": ["a1", "a2"],
        "swot": {"strengths": ["s1", "s2", "s3"], "weaknesses": ["w1"],
                 "opportunities": ["o1"], "threats": ["t1", "t2", "t3"]},
    }),
    "MarketResources": json.dumps({
        "key_players": [{"name": f"P{i}", "url": f"https://p{i}.example", "description": f"d{i}"}
                        for i in range(4)],
        "news_sources": [], "industry_websites": [], "competitor_sites": [],
        "research_papers": [], "relevant_communities": [],
    }),
}

MARKERS = (
    ("MarketMacroAnalysis", "threat_of_new_entrants"),
    ("MarketResources", "relevant_communities"),
    ("MarketSynthesis", "risks_and_assumptions"),
)


class StubLLM:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.schemas: list[str] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        system = str(messages[0].content)
        name = next((n for n, marker in MARKERS if marker in system), None)
        reply = ""
        if name is not None:
            self.schemas.append(name)
            reply = "not json" if name in self.fail_on else REPLIES[name]

        class Msg:
            content = reply
            usage_metadata = {"input_tokens": 10, "output_tokens": 5}

        return Msg()


class StubGenerator:
    def __init__(self, out: Path, fail: bool = False) -> None:
        self.out = out
        self.fail = fail
        self.rendered: list[MarketResearchResult] = []

    def generate_pdf(self, result: MarketResearchResult) -> Path:
        if self.fail:
            raise RuntimeError("pango missing")
        self.rendered.append(result)
        p = self.out / "mr.pdf"
        p.write_bytes(b"%PDF")
        return p

    def generate_docx(self, result: MarketResearchResult) -> Path:
        p = self.out / "mr.docx"
        p.write_bytes(b"docx")
        return p


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "jobs_db", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(settings, "enable_stage_critic", False)
    built: list[MarketResearchService] = []

    def build(fail_on: set[str] | None = None, render_fails: bool = False) -> MarketResearchService:
        llm = StubLLM(fail_on)

        def factory(model: str, temperature: float, max_tokens: int) -> LLMClient:
            client = LLMClient(model="test-model")
            client._llm = llm  # type: ignore[assignment]
            return client

        svc = MarketResearchService(runner=AgentRunner(client_factory=factory))
        svc.llm = llm  # type: ignore[attr-defined]
        svc._report_gen = StubGenerator(tmp_path, fail=render_fails)  # type: ignore[assignment]
        built.append(svc)
        return svc

    yield build
    for svc in built:
        await svc._store.close()


async def _run(svc: MarketResearchService) -> None:
    await svc._store.init()
    await svc._store.create_market_research_job("mr1", "Developer tooling")
    await svc.run("mr1", "Developer tooling")


async def _stages(svc: MarketResearchService) -> dict[str, dict[str, Any]]:
    return {s["stage"]: s for s in await svc.get_stages("mr1")}


class TestHappyPath:
    async def test_completes_with_every_stage_recorded(self, service) -> None:
        svc = service()
        await _run(svc)
        assert (await svc.get_status("mr1"))["status"] == JobStatus.COMPLETED
        stages = await _stages(svc)
        assert set(stages) == {"macro", "sizing", "resources", "render"}
        assert all(s["status"] == StageStatus.SUCCEEDED for s in stages.values())

    async def test_macro_and_swot_reach_the_reader(self, service) -> None:
        """They were scored and reviewed, then dropped on assembly."""
        svc = service()
        await _run(svc)
        result = MarketResearchResult(**json.loads((await svc.get_result("mr1"))["result_json"]))
        assert result.pestel is not None and result.pestel.legal == ["legal factor"]
        assert result.five_forces is not None and result.five_forces.buyer_power == "High"
        assert result.swot is not None and result.swot.threats == ["t1", "t2", "t3"]
        rendered = svc._report_gen.rendered[0]
        assert rendered.pestel is not None


class TestRenderFailure:
    async def test_a_render_failure_is_partial_and_keeps_the_analysis(self, service) -> None:
        svc = service(render_fails=True)
        await _run(svc)
        row = await svc.get_result("mr1")
        assert row["status"] == JobStatus.PARTIAL
        assert "could not be rendered" in row["error"]
        assert row["result_json"]
        assert row["pdf_path"] is None
        render = (await _stages(svc))["render"]
        assert render["status"] == StageStatus.FAILED
        assert "pango missing" in render["error"]


class TestStageFailure:
    async def test_a_failed_stage_fails_the_job_and_names_itself(self, service) -> None:
        svc = service(fail_on={"MarketSynthesis"})
        await _run(svc)
        assert (await svc.get_status("mr1"))["status"] == JobStatus.FAILED
        stages = await _stages(svc)
        assert stages["sizing"]["status"] == StageStatus.FAILED
        assert "resources" not in stages


class TestRetry:
    async def test_resume_reuses_the_finished_prefix(self, service) -> None:
        svc = service(fail_on={"MarketSynthesis"})
        await _run(svc)
        svc.llm.fail_on = set()
        svc.llm.schemas.clear()
        await svc._store.requeue_market_research_for_retry("mr1")
        await svc.run("mr1", "Developer tooling", resume=True)
        assert (await svc.get_status("mr1"))["status"] == JobStatus.COMPLETED
        assert svc.llm.schemas == ["MarketSynthesis", "MarketResources"]

    async def test_a_completed_job_cannot_be_retried(self, service) -> None:
        from app.orchestrator.bi_pipeline import JobStateError

        svc = service()
        await _run(svc)
        with pytest.raises(JobStateError):
            await svc.retry("mr1")
