"""End-to-end pipeline tests with a stubbed LLM, scraper and mailer.

These cover the job lifecycle defects the rewrite targeted:
  - a failing stage is recorded, naming the stage and the reason
  - the job is not marked complete before delivery is attempted
  - a delivery failure yields `partial`, not `complete` or `failed`
  - stage rows carry timings, models, attempts and quality scores
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.analysis import prompts
from app.analysis.runner import AgentRunner
from app.analysis.specs import SWOT_SPEC
from app.config import settings
from app.email.message import DeliveryResult
from app.llm.client import LLMClient
from app.models.bi import ScrapedContent
from app.orchestrator.bi_pipeline import BIPipeline
from app.pipeline.eta import elapsed_ms
from app.pipeline.stages import JobStatus, StageStatus

PAGE = ScrapedContent(
    url="https://acme.example.com",
    title="Acme Analytics",
    description="E-commerce analytics for growing retailers",
    headings=["H1: Acme Analytics", "H2: Pricing"],
    text_content="Acme Analytics builds e-commerce analytics dashboards. " * 40,
    meta_keywords=["analytics", "ecommerce"],
)

# One reply per agent stage, in pipeline order.
REPLIES = {
    "BusinessProfile": json.dumps({
        "company_name": "Acme Analytics", "tagline": "See your store",
        "description": "Analytics for retailers", "industry": "E-commerce analytics tooling",
        "offerings": ["Dashboards", "Alerts", "Reports"],
        "target_audience": ["SME retailers"], "value_proposition": "Clarity on store metrics",
        "contact_info": {}, "social_links": [], "website": "https://acme.example.com",
    }),
    "MarketAnalysis": json.dumps({
        "target_market": "SME e-commerce", "market_size": "$1B-$3B",
        "market_trends": ["headless commerce", "first-party data", "AI insights"],
        "customer_segments": ["Shopify sellers", "DTC brands"],
        "pricing_strategy": "tiered", "market_positioning": "mid-market analytics",
    }),
    "CompetitiveAnalysis": json.dumps({
        "direct_competitors": [
            {"name": "Triple Whale"}, {"name": "Polar Analytics"}, {"name": "Lifetimely"}],
        "indirect_competitors": [{"name": "Google Analytics"}, {"name": "Looker"}],
        "competitive_advantages": ["vertical focus", "fast onboarding"],
        "market_gaps": ["SMB pricing", "attribution clarity"],
        "threat_assessment": "Rivalry is high among analytics vendors serving SMB "
                             "e-commerce, with low switching costs and heavy discounting.",
    }),
    "SWOTAnalysis": json.dumps({
        "strengths": ["a", "b", "c", "d", "e"], "weaknesses": ["f", "g", "h", "i", "j"],
        "opportunities": ["k", "l", "m", "n", "o"], "threats": ["p", "q", "r", "s", "t"],
    }),
    "MarketingAnalysis": json.dumps({
        "personas": [
            {"name": "Store owner Sam", "description": "Runs a DTC brand",
             "pain_points": ["no time"], "goals": ["grow"], "channels": ["email"]},
            {"name": "Analyst Ana", "description": "In-house analyst",
             "pain_points": ["messy data"], "goals": ["clean reports"], "channels": ["search"]}],
        "brand_personality": {"archetype": "Sage", "traits": ["clear"], "voice_characteristics": "plain"},
        "faq": [{"question": f"q{i}", "answer": f"a{i}"} for i in range(5)],
        "customer_journey": [
            {"stage": s, "description": "d", "touchpoints": ["t"]}
            for s in ("Awareness", "Consideration", "Decision", "Retention")],
        "eeat": {"experience": ["x"], "expertise": ["y"], "authoritativeness": ["z"],
                 "trustworthiness": ["w"]},
        "geo_tactics": [{"tactic": "schema", "description": "add markup"},
                        {"tactic": "faq", "description": "answer questions"}],
        "call_to_action": "Start a free trial",
    }),
    "BIReport": json.dumps({
        "executive_summary": "Acme Analytics serves SME e-commerce retailers. " * 20,
        "business_profile": {
            "company_name": "Acme Analytics", "tagline": "", "description": "",
            "industry": "SaaS", "value_proposition": ""},
        "market_analysis": {
            "target_market": "SME", "market_size": "$1B", "pricing_strategy": "tiered",
            "market_positioning": "mid-market"},
        "competitive_analysis": {"threat_assessment": "high"},
        "swot_analysis": {},
        "recommendations": ["r1", "r2", "r3", "r4", "r5"],
        "risk_factors": ["k1", "k2", "k3"],
    }),
}


class SchemaAwareLLM:
    """Returns the reply matching whichever schema the prompt asked for."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls = 0

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls += 1
        # Only the system message carries the requested schema. The user prompt
        # embeds earlier stages' JSON, so matching on it picks the wrong schema.
        system = str(messages[0].content) if messages else ""
        name = _identify_schema(system)
        reply = "" if name is None else REPLIES[name]
        if name is not None and name == self.fail_on:
            reply = "not valid json at all"

        class Msg:
            content = reply
            usage_metadata = {"input_tokens": 200, "output_tokens": 90}

        return Msg()


# Most-nested schemas first: BIReport embeds BusinessProfile and MarketAnalysis,
# so its own marker has to be tested before theirs.
SCHEMA_MARKERS = (
    ("BIReport", "risk_factors"),
    ("MarketingAnalysis", "geo_tactics"),
    ("CompetitiveAnalysis", "indirect_competitors"),
    ("SWOTAnalysis", "opportunities"),
    ("MarketAnalysis", "market_positioning"),
    ("BusinessProfile", "value_proposition"),
)


def _identify_schema(system_message: str) -> str | None:
    for name, marker in SCHEMA_MARKERS:
        if marker in system_message:
            return name
    return None


class StubReportGenerator:
    def __init__(self, out: Path) -> None:
        self.out = out

    def generate_pdf(self, *args: Any, **kwargs: Any) -> Path:
        p = self.out / "report.pdf"
        p.write_bytes(b"%PDF-1.4 stub")
        return p

    def generate_docx(self, *args: Any, **kwargs: Any) -> Path:
        p = self.out / "report.docx"
        p.write_bytes(b"stub docx")
        return p


class StubEmail:
    """Stands in for EmailService, which now returns a DeliveryResult."""

    def __init__(self, ok: bool = True, raises: bool = False) -> None:
        self.ok = ok
        self.raises = raises
        self.sent: list[str] = []

    async def send_report(self, to_email: str, **kwargs: Any) -> DeliveryResult:
        if self.raises:
            # EmailService converts a provider exception into a failed result;
            # mirror that rather than letting it escape into the pipeline.
            return DeliveryResult.failure(
                "stub", "smtp connection refused", retryable=True
            )
        self.sent.append(to_email)
        if self.ok:
            return DeliveryResult.success("stub", message_id="stub-1")
        return DeliveryResult.failure("stub", "The email provider rejected the message")


@pytest.fixture
async def pipeline(tmp_path, monkeypatch):
    """A pipeline wired to stubs, on its own SQLite file."""
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "jobs_db", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(settings, "enable_stage_critic", False)

    def build(scraped: ScrapedContent = PAGE, fail_on=None, email=None):
        llm = SchemaAwareLLM(fail_on=fail_on)

        def factory(model: str, temperature: float, max_tokens: int) -> LLMClient:
            client = LLMClient(model="test-model")
            client._llm = llm  # type: ignore[assignment]
            return client

        p = BIPipeline(runner=AgentRunner(client_factory=factory))
        p._scraper.scrape = _const(scraped)  # type: ignore[method-assign]
        p._report_gen = StubReportGenerator(tmp_path)  # type: ignore[assignment]
        p._email_service = email or StubEmail()  # type: ignore[assignment]
        built.append(p)
        return p

    built: list[BIPipeline] = []
    yield build
    for p in built:
        await p.close()  # release the aiosqlite worker thread


def _const(value):
    async def scrape(_url: str):
        return value

    return scrape


async def _run(p: BIPipeline, url: str = "https://acme.example.com") -> str:
    await p.init()
    job = await p._store.create_job("job1", url, "user@example.com")
    await p.run("job1", url, "user@example.com")
    return job["job_id"]


def _by_slug(stages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["stage"]: s for s in stages}


class TestHappyPath:
    async def test_job_completes(self, pipeline) -> None:
        p = pipeline()
        await _run(p)
        job = await p.get_job("job1")
        assert job["status"] == JobStatus.COMPLETED

    async def test_every_stage_succeeded(self, pipeline) -> None:
        p = pipeline()
        await _run(p)
        stages = _by_slug(await p.get_stages("job1"))
        assert set(stages) == {
            "scrape", "profile", "market", "competitive",
            "swot", "marketing", "report", "render", "deliver",
        }
        assert all(s["status"] == StageStatus.SUCCEEDED for s in stages.values())

    async def test_stage_rows_carry_metrics(self, pipeline) -> None:
        p = pipeline()
        await _run(p)
        profile = _by_slug(await p.get_stages("job1"))["profile"]
        # The configured model for the stage, not whatever the stub client used.
        assert profile["model"] == settings.agent_model_business_profile
        # Against the constant, not a literal: a prompt revision should bump the
        # version, not break this test. What matters is that it is recorded, so
        # a stored result stays attributable to the prompt that produced it.
        assert profile["prompt_version"] == prompts.BUSINESS_PROFILE_V
        assert profile["prompt_version"].startswith("profile/")
        assert profile["attempts"] == 1
        assert profile["duration_ms"] is not None
        assert profile["prompt_tokens"] == 200
        assert profile["completeness"] == 100.0

    async def test_stages_are_ordered(self, pipeline) -> None:
        p = pipeline()
        await _run(p)
        stages = await p.get_stages("job1")
        assert [s["stage"] for s in stages][:3] == ["scrape", "profile", "market"]

    async def test_artifact_snapshot_persisted_per_stage(self, pipeline) -> None:
        """Enables retry-from-stage without redoing earlier work."""
        p = pipeline()
        await _run(p)
        raw = await p._store.get_stage_artifact("job1", "profile")
        assert raw is not None
        assert json.loads(raw)["company_name"] == "Acme Analytics"

    async def test_email_was_sent(self, pipeline) -> None:
        email = StubEmail()
        p = pipeline(email=email)
        await _run(p)
        assert email.sent == ["user@example.com"]


class TestDeliveryFailure:
    async def test_rejected_email_yields_partial(self, pipeline) -> None:
        p = pipeline(email=StubEmail(ok=False))
        await _run(p)
        job = await p.get_job("job1")
        assert job["status"] == JobStatus.PARTIAL

    async def test_raised_email_error_yields_partial(self, pipeline) -> None:
        p = pipeline(email=StubEmail(raises=True))
        await _run(p)
        job = await p.get_job("job1")
        assert job["status"] == JobStatus.PARTIAL

    async def test_raising_provider_is_absorbed_by_the_service(self, pipeline) -> None:
        """A provider that throws must still land as `partial`, not an exception."""
        from app.email.message import EmailMessage
        from app.email.service import EmailService

        class Exploding:
            name = "exploding"

            async def send(self, msg: EmailMessage, from_email: str) -> DeliveryResult:
                raise RuntimeError("provider blew up")

        p = pipeline(email=EmailService(provider=Exploding()))
        await _run(p)
        job = await p.get_job("job1")
        assert job["status"] == JobStatus.PARTIAL

    async def test_delivery_attempt_is_recorded(self, pipeline) -> None:
        p = pipeline(email=StubEmail(ok=False))
        await _run(p)
        rows = await p._store.get_deliveries("job1")
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["to_email"] == "user@example.com"

    async def test_delivery_record_counts_real_attempts(self, pipeline) -> None:
        """Not the configured maximum: a one-shot failure is not three tries."""
        p = pipeline(email=StubEmail(ok=False))
        await _run(p)
        rows = await p._store.get_deliveries("job1")
        assert rows[0]["attempts"] == 1

    async def test_delivery_record_agrees_with_the_stage_row(self, pipeline) -> None:
        p = pipeline(email=StubEmail(ok=False))
        await _run(p)
        rows = await p._store.get_deliveries("job1")
        deliver = _by_slug(await p.get_stages("job1"))["deliver"]
        assert rows[0]["attempts"] == deliver["attempts"]

    async def test_successful_delivery_is_recorded(self, pipeline) -> None:
        p = pipeline()
        await _run(p)
        rows = await p._store.get_deliveries("job1")
        assert rows[0]["status"] == "sent"
        assert rows[0]["message_id"] == "stub-1"

    async def test_deliver_stage_is_marked_failed(self, pipeline) -> None:
        p = pipeline(email=StubEmail(raises=True))
        await _run(p)
        deliver = _by_slug(await p.get_stages("job1"))["deliver"]
        assert deliver["status"] == StageStatus.FAILED
        assert "smtp connection refused" in deliver["error"]

    async def test_report_is_still_available(self, pipeline) -> None:
        """A failed send must not cost the user the report."""
        p = pipeline(email=StubEmail(ok=False))
        await _run(p)
        job = await p.get_job("job1")
        assert job["pdf_path"] and Path(job["pdf_path"]).exists()
        assert job["docx_path"] and Path(job["docx_path"]).exists()

    async def test_analysis_stages_still_succeeded(self, pipeline) -> None:
        p = pipeline(email=StubEmail(ok=False))
        await _run(p)
        stages = _by_slug(await p.get_stages("job1"))
        assert stages["report"]["status"] == StageStatus.SUCCEEDED
        assert stages["render"]["status"] == StageStatus.SUCCEEDED


class TestStageFailure:
    async def test_failing_agent_names_the_stage(self, pipeline) -> None:
        p = pipeline(fail_on="SWOTAnalysis")
        await _run(p)
        stages = _by_slug(await p.get_stages("job1"))
        assert stages["swot"]["status"] == StageStatus.FAILED
        assert stages["swot"]["error_kind"] == "parse_error"
        assert stages["swot"]["error"]

    async def test_job_is_failed_not_stuck_running(self, pipeline) -> None:
        p = pipeline(fail_on="SWOTAnalysis")
        await _run(p)
        job = await p.get_job("job1")
        assert job["status"] == JobStatus.FAILED

    async def test_earlier_stages_keep_their_results(self, pipeline) -> None:
        p = pipeline(fail_on="SWOTAnalysis")
        await _run(p)
        stages = _by_slug(await p.get_stages("job1"))
        assert stages["profile"]["status"] == StageStatus.SUCCEEDED
        assert stages["market"]["status"] == StageStatus.SUCCEEDED

    async def test_later_stages_never_started(self, pipeline) -> None:
        p = pipeline(fail_on="SWOTAnalysis")
        await _run(p)
        assert "report" not in _by_slug(await p.get_stages("job1"))

    async def test_no_email_on_failure(self, pipeline) -> None:
        email = StubEmail()
        p = pipeline(fail_on="SWOTAnalysis", email=email)
        await _run(p)
        assert email.sent == []


class TestFailedStageObservability:
    """A failed stage keeps the numbers needed to diagnose it.

    These were previously discarded: the failure path wrote only the status and
    the message, so the one stage anybody needed to inspect was the one with no
    model, no duration, no attempt count and no token cost.
    """

    async def test_failed_agent_stage_keeps_its_metrics(self, pipeline) -> None:
        p = pipeline(fail_on="SWOTAnalysis")
        await _run(p)
        swot = _by_slug(await p.get_stages("job1"))["swot"]
        assert swot["status"] == StageStatus.FAILED
        assert swot["model"] == SWOT_SPEC.model
        assert swot["attempts"] == 3
        assert swot["duration_ms"] is not None
        assert swot["prompt_tokens"] and swot["completion_tokens"]

    async def test_failed_stage_reports_no_quality_score(self, pipeline) -> None:
        """The contract never ran, so completeness would be a fiction."""
        p = pipeline(fail_on="SWOTAnalysis")
        await _run(p)
        assert _by_slug(await p.get_stages("job1"))["swot"]["completeness"] is None

    async def test_blocked_scrape_keeps_its_duration(self, pipeline) -> None:
        blocked = ScrapedContent(
            url="http://127.0.0.1", title="", description="", text_content="",
            error="Refusing to fetch 127.0.0.1 (127.0.0.1): loopback address",
        )
        p = pipeline(scraped=blocked)
        await _run(p, "http://127.0.0.1")
        scrape = _by_slug(await p.get_stages("job1"))["scrape"]
        assert scrape["status"] == StageStatus.FAILED
        assert scrape["duration_ms"] is not None
        assert scrape["attempts"] == 1

    async def test_failed_delivery_records_real_attempt_count(self, pipeline) -> None:
        p = pipeline(email=StubEmail(ok=False))
        await _run(p)
        deliver = _by_slug(await p.get_stages("job1"))["deliver"]
        assert deliver["status"] == StageStatus.FAILED
        assert deliver["attempts"] == 1
        assert deliver["duration_ms"] is not None

    async def test_failed_job_reports_nonzero_elapsed_time(self, pipeline) -> None:
        """Elapsed sums stage durations, so a failure used to display as 0s."""
        p = pipeline(fail_on="SWOTAnalysis")
        await _run(p)
        assert elapsed_ms(await p.get_stages("job1")) > 0


class TestScrapeFailure:
    async def test_blocked_url_is_recorded_as_blocked(self, pipeline) -> None:
        blocked = ScrapedContent(
            url="http://127.0.0.1", title="", description="", text_content="",
            error="Refusing to fetch 127.0.0.1 (127.0.0.1): loopback address",
        )
        p = pipeline(scraped=blocked)
        await _run(p, "http://127.0.0.1")
        stages = _by_slug(await p.get_stages("job1"))
        assert stages["scrape"]["status"] == StageStatus.FAILED
        assert stages["scrape"]["error_kind"] == "blocked"

    async def test_thin_page_scores_low_but_proceeds(self, pipeline) -> None:
        thin = ScrapedContent(
            url="https://acme.example.com", title="", description="",
            text_content="hi", headings=[],
        )
        p = pipeline(scraped=thin)
        await _run(p)
        scrape = _by_slug(await p.get_stages("job1"))["scrape"]
        assert scrape["status"] == StageStatus.SUCCEEDED
        assert scrape["completeness"] < 50


class TestContractScoring:
    async def test_thin_swot_scores_below_full(self, pipeline, monkeypatch) -> None:
        """The prompt demands 5 per quadrant; nothing used to check it."""
        monkeypatch.setitem(
            REPLIES, "SWOTAnalysis",
            json.dumps({"strengths": ["a"], "weaknesses": ["b"],
                        "opportunities": ["c"], "threats": ["d"]}),
        )
        p = pipeline()
        await _run(p)
        swot = _by_slug(await p.get_stages("job1"))["swot"]
        assert swot["status"] == StageStatus.SUCCEEDED  # scored, not enforced
        assert swot["completeness"] == 0.0
        assert len(json.loads(swot["warnings"])) == 4


class TestInterruptedRecovery:
    async def test_running_jobs_become_interrupted(self, pipeline) -> None:
        p = pipeline()
        await p.init()
        await p._store.create_job("stale", "https://x.example.com", "u@example.com")
        await p._store.update_status("stale", JobStatus.RUNNING)
        await p._store.start_stage("stale", "bi", "profile", 1)

        count = await p.recover_interrupted()

        assert count >= 1
        job = await p.get_job("stale")
        assert job["status"] == JobStatus.INTERRUPTED
        assert "restarted" in job["error"]
        stage = _by_slug(await p.get_stages("stale"))["profile"]
        assert stage["status"] == StageStatus.FAILED
