"""Rendering whole reports, so a template or DOCX regression fails a test, not a job."""

from pathlib import Path

import pytest
from docx import Document

from app.models.bi import (
    BrandPersonalityMatrix,
    BusinessProfile,
    CompetitiveAnalysis,
    EEATSignal,
    FAQItem,
    FiveForces,
    GEOResult,
    MarketAnalysis,
    MarketingAnalysis,
    MarketResearchResult,
    MarketResource,
    PESTELAnalysis,
    ReportSynthesis,
    SWOTAnalysis,
    TargetAudiencePersona,
    build_report,
)
from app.report.generator import MarketResearchReportGenerator, ReportGenerator


def _bi() -> tuple:
    profile = BusinessProfile(
        company_name="Acme Analytics", tagline="See your store", description="Analytics",
        industry="E-commerce analytics tooling", offerings=["Dashboards"],
        target_audience=["SME retailers"], value_proposition="Clarity",
    )
    market = MarketAnalysis(
        target_market="SME e-commerce", market_size="$1B-$3B", market_trends=["AI insights"],
        customer_segments=["Shopify sellers"], pricing_strategy="Tiered",
        market_positioning="Mid-market",
    )
    competitive = CompetitiveAnalysis(
        direct_competitors=[{"name": "Triple Whale", "offering": "attribution"}],
        indirect_competitors=[{"name": "Looker", "description": "general BI"}],
        competitive_advantages=["vertical focus"], market_gaps=["SMB pricing"],
        threat_assessment="High rivalry.",
    )
    swot = SWOTAnalysis(strengths=["s"], weaknesses=["w"], opportunities=["o"], threats=["t"])
    synthesis = ReportSynthesis(
        executive_summary="Acme serves retailers.", recommendations=["Raise prices"],
        risk_factors=["Churn"],
    )
    marketing = MarketingAnalysis(
        personas=[TargetAudiencePersona(name="Sam", description="Owner", pain_points=["time", "data"])],
        brand_personality=BrandPersonalityMatrix(archetype="Sage", traits=["clear"]),
        faq=[FAQItem(question="Is it fast?", answer="Yes")],
        eeat=EEATSignal(expertise=["Founded by analysts"]),
        geo_tactics=[GEOResult(tactic="Schema", description="Add markup")],
        call_to_action="Start a trial",
    )
    return build_report(synthesis, profile, market, competitive, swot), profile, marketing


def _research() -> MarketResearchResult:
    return MarketResearchResult(
        market_query="EV charging", executive_summary="Growing.", summary="Landscape.",
        market_size="TAM: $10B · SAM: $2B", growth_rate="12%", key_insights=["i"],
        key_players=[MarketResource(name="ChargePoint", url="https://chargepoint.com", description="d")],
        pestel=PESTELAnalysis(political=["Subsidies"], legal=["Standards"]),
        five_forces=FiveForces(buyer_power="High - fleets negotiate"),
        swot=SWOTAnalysis(strengths=["Demand"], weaknesses=["Grid"], opportunities=["Fleets"], threats=["Rates"]),
    )


def _docx_text(path: Path) -> str:
    return "\n".join(p.text for p in Document(str(path)).paragraphs)


class TestBiDocx:
    def test_every_stage_section_reaches_the_document(self, tmp_path: Path) -> None:
        report, profile, marketing = _bi()
        path = ReportGenerator(output_dir=str(tmp_path)).generate_docx(
            report, profile, profile.company_name, marketing
        )
        text = _docx_text(path)
        for expected in (
            "Acme serves retailers.", "$1B-$3B", "Shopify sellers", "Triple Whale: attribution",
            "Indirect Competitors", "Looker: general BI", "SMB pricing", "High rivalry.",
            "Raise prices", "Churn", "Sam", "Is it fast?", "Start a trial",
        ):
            assert expected in text, expected


class TestResearchDocx:
    def test_macro_and_swot_sections_are_included(self, tmp_path: Path) -> None:
        path = MarketResearchReportGenerator(output_dir=str(tmp_path)).generate_docx(_research())
        text = _docx_text(path)
        for expected in ("Market SWOT", "Grid", "PESTEL Analysis", "Subsidies", "Porter's Five Forces",
                         "High - fleets negotiate", "ChargePoint"):
            assert expected in text, expected

    def test_older_results_without_macro_still_render(self, tmp_path: Path) -> None:
        result = _research().model_copy(update={"pestel": None, "five_forces": None, "swot": None})
        path = MarketResearchReportGenerator(output_dir=str(tmp_path)).generate_docx(result)
        assert "PESTEL" not in _docx_text(path)


class TestPdf:
    @pytest.fixture(autouse=True)
    def _needs_weasyprint(self) -> None:
        try:
            import weasyprint  # noqa: F401
        except OSError:
            pytest.skip("WeasyPrint's native libraries are not installed here")

    def test_bi_pdf_renders(self, tmp_path: Path) -> None:
        report, profile, marketing = _bi()
        path = ReportGenerator(output_dir=str(tmp_path)).generate_pdf(
            report, profile, profile.company_name, marketing
        )
        assert path.read_bytes().startswith(b"%PDF")

    def test_research_pdf_renders(self, tmp_path: Path) -> None:
        path = MarketResearchReportGenerator(output_dir=str(tmp_path)).generate_pdf(_research())
        assert path.read_bytes().startswith(b"%PDF")
