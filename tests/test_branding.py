"""The brand reaches the From line, the email and the PDF, and the house style holds."""

import pytest

from app.branding import brand, display_host, sender, without_em_dashes
from app.config import settings
from app.email import templates
from app.email.templates import ReportDigest
from app.models.bi import (
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketingAnalysis,
    ReportSynthesis,
    SWOTAnalysis,
    build_report,
)
from app.report.generator import market_tiers, paragraphs, render_bi_html, render_research_html

VALCREST_SIZE = (
    "TAM: 45,000–55,000 employers (estimate based on national business registry data); "
    "SAM: 30,000–35,000 employers (estimated segment that uses payroll software); "
    "SOM: 500–1,000 customers (estimated current customer base)."
)


@pytest.fixture(autouse=True)
def _brand(monkeypatch) -> None:
    monkeypatch.setattr(settings, "brand_name", "Rubric")
    monkeypatch.setattr(settings, "brand_tagline", "Business intelligence, read closely.")
    monkeypatch.setattr(settings, "brand_author", "Nicholas Smith")
    monkeypatch.setattr(settings, "brand_author_email", "author@example.com")
    monkeypatch.setattr(settings, "brand_author_url", "https://author.example/")
    monkeypatch.setattr(settings, "from_name", "")


def _report(summary: str = "Acme sells widgets \u2014 fast.\n\nSecond paragraph.", size: str = VALCREST_SIZE):
    profile = BusinessProfile(
        company_name="Acme", tagline="Widgets", description="d", industry="Widget supply",
        offerings=["Widgets"], target_audience=["Factories"], value_proposition="Fast",
        website="https://www.acme.example/",
    )
    market = MarketAnalysis(
        target_market="Factories", market_size=size, market_trends=["Automation"],
        customer_segments=["SMEs"], pricing_strategy="Tiered", market_positioning="Mid",
    )
    competitive = CompetitiveAnalysis(
        direct_competitors=[{"name": "Globex", "offering": "widgets"}],
        indirect_competitors=[{"name": "DIY", "description": "self-build"}, {"name": "Initech"}],
        competitive_advantages=["speed"], market_gaps=["service"], threat_assessment="Moderate.",
    )
    swot = SWOTAnalysis(strengths=["s"], weaknesses=["w"], opportunities=["o"], threats=["t"])
    synthesis = ReportSynthesis(
        executive_summary=summary,
        recommendations=["Raise prices \u2014 carefully", "Hire", "Expand", "Automate"],
        risk_factors=["Churn"],
    )
    return build_report(synthesis, profile, market, competitive, swot), profile


class TestSender:
    def test_the_brand_names_the_sender(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "from_email", "reports@example.com")
        assert sender() == "Rubric <reports@example.com>"

    def test_from_name_wins(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "from_email", "reports@example.com")
        monkeypatch.setattr(settings, "from_name", "Nick at Rubric")
        assert sender() == "Nick at Rubric <reports@example.com>"

    def test_a_name_already_in_from_email_is_kept(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "from_email", "Reports Desk <reports@example.com>")
        assert sender() == "Reports Desk <reports@example.com>"


class TestHouseStyle:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("a\u2014b", "a - b"), ("a \u2014 b", "a - b"), ("10–20", "10–20"), ("plain", "plain")],
    )
    def test_em_dashes_become_spaced_hyphens(self, text: str, expected: str) -> None:
        assert without_em_dashes(text) == expected

    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("https://www.riomed.com/cellmaflex/", "riomed.com"),
            ("http://user@Shop.Example:8080/x", "shop.example"),
            ("", ""),
        ],
    )
    def test_display_host(self, url: str, host: str) -> None:
        assert display_host(url) == host

    def test_portfolio_label_drops_the_scheme(self) -> None:
        assert brand().author_url_label == "author.example"


class TestEmail:
    def _digest(self) -> ReportDigest:
        report, profile = _report()
        return ReportDigest.from_report(
            report, profile, MarketingAnalysis(), source_url="https://www.acme.example/x",
            score=94.1, report_url="https://reports.example/jobs/abc",
        )

    def test_digest_counts_what_the_report_holds(self) -> None:
        digest = self._digest()
        assert digest.competitors == 3
        assert digest.risks == 1
        assert len(digest.recommendations) == 4
        assert digest.summary == "Acme sells widgets \u2014 fast."
        assert digest.host == "acme.example"
        assert digest.has_marketing

    def test_subject_names_the_brand_and_company(self) -> None:
        assert templates.report_subject("Acme") == "Your Rubric report on Acme is ready"

    def test_body_carries_brand_author_score_and_link(self) -> None:
        body = templates.report_html(self._digest())
        assert "Rubric" in body
        assert 'href="mailto:author@example.com"' in body
        assert 'href="https://author.example/"' in body
        assert "Made by Nicholas Smith" in body
        assert ">94<" in body
        assert 'href="https://reports.example/jobs/abc"' in body
        assert "Plus 1 more in the report." in body

    def test_no_link_without_a_public_url(self) -> None:
        report, profile = _report()
        body = templates.report_html(ReportDigest.from_report(report, profile))
        assert "View the report online" not in body

    def test_no_em_dashes_anywhere(self) -> None:
        digest = self._digest()
        for part in (templates.report_html(digest), templates.report_text(digest), templates.report_subject("A\u2014B")):
            assert "\u2014" not in part and "&mdash;" not in part

    def test_author_lines_are_left_out_when_unset(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "brand_author", "")
        monkeypatch.setattr(settings, "brand_author_email", "")
        monkeypatch.setattr(settings, "brand_author_url", "")
        body = templates.report_html(self._digest())
        assert "Made by" not in body and "mailto:" not in body

    def test_plain_text_mirrors_the_html(self) -> None:
        text = templates.report_text(self._digest())
        assert "RUBRIC" in text
        assert "Quality score 94/100" in text
        assert "1. Raise prices - carefully" in text
        assert "View the report online: https://reports.example/jobs/abc" in text
        assert "author@example.com" in text


class TestPdfDesign:
    def test_cover_carries_brand_author_score_and_source(self) -> None:
        report, profile = _report()
        html = render_bi_html(report, profile, MarketingAnalysis(), score=94.1, source_url="https://www.acme.example/x")
        assert ">Rubric<" in html
        assert "By Nicholas Smith" in html
        assert 'href="mailto:author@example.com"' in html
        assert "94 / 100" in html
        assert "acme.example" in html
        assert "Marketing playbook" in html

    def test_no_em_dashes_in_the_document(self) -> None:
        report, profile = _report()
        html = render_bi_html(report, profile)
        assert "\u2014" not in html and "&mdash;" not in html
        assert "Raise prices - carefully" in html

    def test_marketing_part_is_left_out_without_marketing(self) -> None:
        report, profile = _report()
        assert "Marketing playbook" not in render_bi_html(report, profile)

    def test_market_sizing_draws_rings(self) -> None:
        report, profile = _report()
        html = render_bi_html(report, profile)
        assert html.count("<circle") == 3
        assert "45,000–55,000 employers" in html

    def test_unstructured_market_size_is_shown_as_written(self) -> None:
        report, profile = _report(size="$1B-$3B globally")
        html = render_bi_html(report, profile)
        assert "<circle" not in html
        assert "$1B-$3B globally" in html

    def test_blank_items_from_the_model_are_dropped(self) -> None:
        """A model returned "" as a sixth recommendation; it printed as a bare "06"."""
        report, profile = _report()
        report.recommendations.append("  ")
        report.swot_analysis.threats[:] = ["", " "]
        html = render_bi_html(report, profile)
        recs = html.split('<ol class="recs">', 1)[1].split("</ol>", 1)[0]
        assert recs.count("<li>") == 4
        assert '<div class="v">4</div>' in html  # the recommendations figure
        assert "Not covered in this analysis." in html

        digest = ReportDigest.from_report(report, profile)
        assert len(digest.recommendations) == 4

    def test_research_cover_is_branded(self) -> None:
        from app.models.bi import MarketResearchResult

        result = MarketResearchResult(market_query="EV charging", executive_summary="x", summary="y")
        html = render_research_html(result)
        assert ">Rubric<" in html and "Market research report" in html


class TestMarketTiers:
    def test_reads_all_three_with_notes(self) -> None:
        tiers = market_tiers(VALCREST_SIZE)
        assert [t.label for t in tiers] == ["TAM", "SAM", "SOM"]
        assert tiers[0].value == "45,000–55,000 employers"
        assert tiers[0].note == "estimate based on national business registry data"
        assert tiers[2].note == "estimated current customer base"
        assert tiers[0].r > tiers[1].r > tiers[2].r

    def test_middle_dot_separators(self) -> None:
        tiers = market_tiers("TAM: $10B · SAM: $2B")
        assert [(t.label, t.value) for t in tiers] == [("TAM", "$10B"), ("SAM", "$2B")]

    @pytest.mark.parametrize("text", ["", "$1B-$3B", "TAM: $10B only"])
    def test_fewer_than_two_tiers_is_not_a_diagram(self, text: str) -> None:
        assert market_tiers(text) == []


class TestParagraphs:
    def test_blank_lines_split(self) -> None:
        assert paragraphs("One.\n\nTwo.\n\n\nThree.") == ["One.", "Two.", "Three."]

    def test_single_newlines_split_a_single_block(self) -> None:
        assert paragraphs("One.\nTwo.") == ["One.", "Two."]

    def test_empty(self) -> None:
        assert paragraphs("") == []
