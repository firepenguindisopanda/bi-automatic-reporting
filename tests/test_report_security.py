"""Model output is untrusted: it reaches the PDF, the email subject and the body.

The scraped page is chosen by whoever submits the job, and it can steer what the
model writes. These tests pin down that what the model writes stays text.
"""

from pathlib import Path

import pytest

from app.email import templates
from app.models.bi import (
    BusinessProfile,
    CompetitiveAnalysis,
    MarketAnalysis,
    MarketResearchResult,
    ReportSynthesis,
    SWOTAnalysis,
    build_report,
)
from app.report.generator import (
    ASSETS_DIR,
    SERIF_FONT,
    bundled_asset,
    render_bi_html,
    render_research_html,
    write_pdf,
)

INJECTED = '<img src="file:///etc/passwd"><a rel="attachment" href="http://169.254.169.254/">x</a>'


def _report(text: str = INJECTED):
    profile = BusinessProfile(
        company_name=text, tagline=text, description=text, industry=text,
        offerings=[text], target_audience=[text], value_proposition=text,
    )
    market = MarketAnalysis(
        target_market=text, market_size=text, market_trends=[text],
        customer_segments=[text], pricing_strategy=text, market_positioning=text,
    )
    competitive = CompetitiveAnalysis(
        direct_competitors=[{"name": text, "offering": text}],
        indirect_competitors=[{"name": text, "description": text}],
        competitive_advantages=[text], market_gaps=[text], threat_assessment=text,
    )
    swot = SWOTAnalysis(strengths=[text], weaknesses=[text], opportunities=[text], threats=[text])
    synthesis = ReportSynthesis(executive_summary=text, recommendations=[text], risk_factors=[text])
    return build_report(synthesis, profile, market, competitive, swot), profile


class TestTemplateEscaping:
    def test_bi_template_renders_model_output_as_text(self) -> None:
        report, profile = _report()
        html = render_bi_html(report, profile, source_url="https://example.com")
        assert "<img" not in html
        assert 'rel="attachment"' not in html
        assert "&lt;img src=&#34;file:///etc/passwd&#34;&gt;" in html

    def test_market_research_template_renders_model_output_as_text(self) -> None:
        result = MarketResearchResult(
            market_query=INJECTED, executive_summary=INJECTED, summary=INJECTED,
            key_insights=[INJECTED],
        )
        html = render_research_html(result)
        assert "<img" not in html
        assert "&lt;img" in html


class TestBundledAssets:
    """The one exception to 'fetch nothing': the report's own font files."""

    def test_the_bundled_font_is_allowed(self) -> None:
        assert bundled_asset(SERIF_FONT.as_uri()) == SERIF_FONT.resolve()

    def test_a_path_that_climbs_out_is_refused(self) -> None:
        climbing = ASSETS_DIR.as_uri() + "/fonts/../../generator.py"
        assert bundled_asset(climbing) is None

    def test_other_local_files_are_refused(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("x")
        assert bundled_asset(secret.as_uri()) is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "https://example.com/font.woff2",
            "data:text/plain,hello",
            "file://evil.example/share/font.woff2",
        ],
    )
    def test_anything_that_is_not_a_local_file_is_refused(self, url: str) -> None:
        assert bundled_asset(url) is None


class TestPdfFetches:
    def test_the_renderer_fetches_nothing(self, tmp_path: Path, monkeypatch) -> None:
        """Defence in depth: even markup that got through must not be fetched."""
        try:
            import weasyprint  # noqa: F401
        except OSError:
            pytest.skip("WeasyPrint's native libraries are not installed here")

        from weasyprint.urls import URLFetcher

        seen: list[str] = []
        original = URLFetcher.fetch

        def spy(self, url, headers=None):  # type: ignore[no-untyped-def]
            seen.append(url)
            return original(self, url, headers)

        monkeypatch.setattr(URLFetcher, "fetch", spy)
        secret = tmp_path / "secret.txt"
        secret.write_text("do not embed")
        html = (
            f'<html><body><img src="{secret.as_uri()}">'
            f'<a rel="attachment" href="{secret.as_uri()}">a</a>'
            '<img src="http://127.0.0.1:9/probe.png"></body></html>'
        )
        out = tmp_path / "out.pdf"
        write_pdf(html, out)
        assert out.read_bytes().startswith(b"%PDF")
        # The refusing subclass overrides fetch, so the base fetch never runs.
        assert seen == []
        assert b"do not embed" not in out.read_bytes()

    def test_a_full_report_loads_only_its_bundled_font(self, tmp_path: Path, monkeypatch) -> None:
        try:
            import weasyprint  # noqa: F401
        except OSError:
            pytest.skip("WeasyPrint's native libraries are not installed here")

        from weasyprint.urls import URLFetcher

        seen: list[str] = []
        original = URLFetcher.fetch

        def spy(self, url, headers=None):  # type: ignore[no-untyped-def]
            seen.append(url)
            return original(self, url, headers)

        monkeypatch.setattr(URLFetcher, "fetch", spy)
        report, profile = _report()
        write_pdf(render_bi_html(report, profile), tmp_path / "out.pdf")
        assert seen == [SERIF_FONT.resolve().as_uri()]


class TestEmailTemplates:
    @pytest.mark.parametrize(
        "name",
        ["Acme\r\nBcc: victim@example.com", "Acme\nX-Injected: 1", "Acme Header: x"],
    )
    def test_subject_cannot_carry_a_header(self, name: str) -> None:
        subject = templates.report_subject(name)
        assert "\n" not in subject and "\r" not in subject and " " not in subject

    def test_links_are_dropped_from_the_company_name(self) -> None:
        name = "Claim your refund at https://evil.example/login now www.evil.example"
        shown = templates.display_company_name(name)
        assert "https://" not in shown and "www." not in shown

    def test_a_domain_like_trading_name_survives(self) -> None:
        assert templates.display_company_name("Fly.io") == "Fly.io"

    def test_long_names_are_capped(self) -> None:
        shown = templates.display_company_name("A" * 500)
        assert len(shown) <= templates.MAX_COMPANY_NAME_CHARS

    def test_empty_name_falls_back(self) -> None:
        assert templates.display_company_name("\n\t ") == templates.FALLBACK_COMPANY_NAME

    def test_html_body_escapes_the_name(self) -> None:
        body = templates.report_html(
            templates.ReportDigest(company_name='<script>alert(1)</script><a href="http://x">y</a>')
        )
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        assert '<a href="http://x">' not in body

    def test_quoted_model_text_is_escaped_and_unlinked(self) -> None:
        """The summary and recommendations reach the body too, under our sender name."""
        digest = templates.ReportDigest(
            company_name="Acme",
            summary='Great company. Claim your refund at https://evil.example/login <b>now</b>',
            recommendations=('Visit www.evil.example to verify <img src="x">',),
        )
        html_body, text_body = templates.report_html(digest), templates.report_text(digest)
        assert "evil.example" not in html_body and "evil.example" not in text_body
        assert "<b>" not in html_body and "<img" not in html_body
        assert "&lt;b&gt;now" in html_body

    def test_quoted_model_text_is_capped(self) -> None:
        digest = templates.ReportDigest(
            company_name="Acme", summary="word " * 1000, recommendations=("step " * 1000,)
        )
        body = templates.report_text(digest)
        assert len(body) < templates.MAX_SUMMARY_CHARS + templates.MAX_RECOMMENDATION_CHARS + 1200
