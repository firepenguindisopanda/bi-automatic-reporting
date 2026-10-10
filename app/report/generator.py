import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from docx import Document
from docx.shared import Pt
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from app.branding import PALETTE, brand, display_host, without_em_dashes
from app.models.bi import BIReport, BusinessProfile, MarketingAnalysis, MarketResearchResult

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
ASSETS_DIR = Path(__file__).parent / "assets"
SERIF_FONT = ASSETS_DIR / "fonts" / "source-serif-4-latin.woff2"

MAX_TITLE_CHARS = 90


def _house_style(value: Any) -> Any:
    """Applied to every ``{{ }}`` before escaping. Macro output is already done."""
    if isinstance(value, str) and not isinstance(value, Markup):
        return without_em_dashes(value)
    return value


# Every value in these templates is model output, and the model read an
# attacker-chosen web page. Autoescape keeps that text as text: without it, a
# page that talks the model into emitting `<img src="file:///etc/passwd">`
# gets markup into the PDF, and WeasyPrint dutifully fetches it.
_JINJA = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    finalize=_house_style,
    trim_blocks=True,
    lstrip_blocks=True,
)

HTML_TEMPLATE = _JINJA.get_template("bi.html")
MR_HTML_TEMPLATE = _JINJA.get_template("research.html")


@dataclass(frozen=True, slots=True)
class SizeTier:
    """One ring of the TAM/SAM/SOM diagram, with its geometry."""

    label: str
    long: str
    value: str
    note: str
    r: int
    cy: int
    label_y: int
    fill: str
    stroke: str
    text: str


_TIER_NAMES = {
    "TAM": "Total addressable market",
    "SAM": "Serviceable available market",
    "SOM": "Serviceable obtainable market",
}
_TIER = re.compile(r"\b(TAM|SAM|SOM)\b\s*[:=]?\s*")
_TRAILING = re.compile(r"[\s;,|·.]+$")
_NOTE = re.compile(r"^(.*?)\s*\(([^()]*)\)$", re.S)
# Outer to inner: radius, fill, stroke, text. Circles share a bottom tangent.
_RINGS = (
    (58, "sunk", "rule_strong", "ink_muted"),
    (39, "accent_soft", "rule", "accent"),
    (20, "accent", "accent", "paper"),
)


def market_tiers(text: str) -> list[SizeTier]:
    """TAM/SAM/SOM parsed out of the market-size sentence, outermost first.

    The model writes them as one string ("TAM: 45,000 employers (registry
    data); SAM: ..."). Fewer than two tiers is not a diagram, so it returns []
    and the template shows the sentence as written.
    """
    found: dict[str, tuple[str, str]] = {}
    matches = list(_TIER.finditer(text or ""))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = _TRAILING.sub("", text[match.end() : end]).strip()
        if not chunk or match[1] in found:
            continue
        note_match = _NOTE.match(chunk)
        value, note = (note_match[1], note_match[2]) if note_match else (chunk, "")
        found[match[1]] = (value.strip()[:80], note.strip()[:160])

    present = [label for label in ("TAM", "SAM", "SOM") if label in found]
    if len(present) < 2:
        return []
    rings = _RINGS if len(present) == 3 else (_RINGS[0], _RINGS[2])
    tiers = []
    for label, (r, fill, stroke, colour) in zip(present, rings, strict=True):
        cy = 118 - r
        label_y = cy + 2 if r == rings[-1][0] else cy - r + 10
        value, note = found[label]
        tiers.append(SizeTier(
            label=label, long=_TIER_NAMES[label], value=value, note=note, r=r, cy=cy,
            label_y=label_y, fill=PALETTE[fill], stroke=PALETTE[stroke], text=PALETTE[colour],
        ))
    return tiers


def paragraphs(text: str) -> list[str]:
    """Paragraphs as the model wrote them; one block if it wrote one."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) == 1 and "\n" in parts[0]:
        parts = [p.strip() for p in parts[0].splitlines() if p.strip()]
    return parts


def _competitors(report: BIReport) -> list[tuple[str, str, str]]:
    """(name, Direct/Indirect, offering) rows, from whichever keys the model used."""
    rows = []
    for kind, items in (
        ("Direct", report.competitive_analysis.direct_competitors),
        ("Indirect", report.competitive_analysis.indirect_competitors),
    ):
        for c in items:
            if isinstance(c, dict):
                name = c.get("name") or "Unnamed competitor"
                rows.append((name, kind, c.get("offering") or c.get("description") or ""))
            else:
                rows.append((str(c), kind, ""))
    return rows


def _pretty_date(when: datetime) -> str:
    return f"{when.day} {when:%B %Y}"


def _cover(
    *, title: str, subtitle: str, kind: str, eyebrow: str, source_url: str,
    score: float | None, generated: datetime | None,
) -> dict[str, Any]:
    clipped = title if len(title) <= MAX_TITLE_CHARS else title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return {
        "brand": brand(),
        "c": PALETTE,
        "serif_font_url": SERIF_FONT.as_uri(),
        "title": clipped,
        "subtitle": subtitle,
        "kind": kind,
        "cover_eyebrow": eyebrow,
        "date": _pretty_date(generated or datetime.now()),
        "source_host": display_host(source_url),
        "score": None if score is None else round(score),
    }


def render_bi_html(
    report: BIReport,
    profile: BusinessProfile,
    marketing: MarketingAnalysis | None = None,
    *,
    company: str = "",
    score: float | None = None,
    source_url: str = "",
    generated: datetime | None = None,
) -> str:
    return HTML_TEMPLATE.render(
        **_cover(
            title=company or profile.company_name, subtitle=profile.tagline,
            kind="Business intelligence", eyebrow="Strategic report",
            source_url=source_url or profile.website, score=score, generated=generated,
        ),
        summary_paragraphs=paragraphs(report.executive_summary),
        profile=profile,
        market=report.market_analysis,
        sizing=market_tiers(report.market_analysis.market_size),
        competitive=report.competitive_analysis,
        competitors=_competitors(report),
        swot=report.swot_analysis,
        recommendations=report.recommendations,
        risk_factors=report.risk_factors,
        marketing=marketing,
    )


def render_research_html(result: MarketResearchResult, *, generated: datetime | None = None) -> str:
    return MR_HTML_TEMPLATE.render(
        **_cover(
            title=result.market_query, subtitle="", kind="Market research",
            eyebrow="Market research report", source_url="", score=None, generated=generated,
        ),
        summary_paragraphs=paragraphs(result.executive_summary),
        landscape_paragraphs=paragraphs(result.summary),
        key_insights=result.key_insights,
        recommendations=result.recommendations,
        risks_and_assumptions=result.risks_and_assumptions,
        market_size=result.market_size,
        sizing=market_tiers(result.market_size),
        growth_rate=result.growth_rate,
        swot=result.swot,
        pestel_rows=[(label, items) for label, items in _pestel_rows(result) if items],
        five_forces_rows=[(label, text) for label, text in _five_forces_rows(result) if text],
        resource_groups=[
            (heading, items)
            for heading, items in (
                ("Key players", result.key_players),
                ("Competitor sites", result.competitor_sites),
                ("Industry websites and resources", result.industry_websites),
                ("News and publications", result.news_sources),
                ("Research papers and reports", result.research_papers),
                ("Communities and forums", result.relevant_communities),
            )
            if items
        ],
    )


def _apply_house_style(doc: Any) -> None:
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            if "—" in run.text:
                run.text = without_em_dashes(run.text)


def _sanitize_filename(name: str) -> str:
    result = re.sub(r"[^\w\s-]", "", name).strip()
    result = re.sub(r"[-\s]+", "_", result)
    return result or "report"


def _pestel_rows(result: MarketResearchResult) -> list[tuple[str, list[str]]]:
    p = result.pestel
    if p is None:
        return []
    return [
        ("Political", p.political),
        ("Economic", p.economic),
        ("Social", p.social),
        ("Technological", p.technological),
        ("Environmental", p.environmental),
        ("Legal", p.legal),
    ]


def _five_forces_rows(result: MarketResearchResult) -> list[tuple[str, str]]:
    f = result.five_forces
    if f is None:
        return []
    return [
        ("Threat of new entrants", f.threat_of_new_entrants),
        ("Supplier power", f.supplier_power),
        ("Buyer power", f.buyer_power),
        ("Threat of substitutes", f.threat_of_substitutes),
        ("Competitive rivalry", f.competitive_rivalry),
    ]


def _competitor_line(c: object) -> str:
    """'Name: what they offer', from whichever keys the model used."""
    if not isinstance(c, dict):
        return str(c)
    name = c.get("name") or "Unnamed competitor"
    offering = c.get("offering") or c.get("description") or ""
    return f"{name}: {offering}" if offering else str(name)


def bundled_asset(url: str) -> Path | None:
    """The file a URL names if it is one of the report's bundled assets, else None.

    Resolved before the check, so `..` and symlinks cannot step outside.
    """
    parsed = urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    try:
        path = Path(url2pathname(parsed.path)).resolve()
    except (OSError, ValueError):
        return None
    if not path.is_relative_to(ASSETS_DIR.resolve()) or not path.is_file():
        return None
    return path


def write_pdf(html: str, filepath: Path) -> None:
    """Render HTML to a PDF file, loading nothing but the bundled fonts.

    WeasyPrint's default fetcher follows http(s) and file:// URLs, which would
    make the renderer a server-side request forgery and local-file read
    reachable from the scraped page. The templates need one thing from outside
    the document, the serif font, so the fetcher serves files under
    ``assets/`` and refuses every other URL. Autoescaping stops injected markup;
    this stops the fetch even if markup ever gets through.

    Imported lazily: WeasyPrint loads Pango when imported, and importing it at
    module load made the whole API unimportable on a machine without it.
    """
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    class _BundledAssetsOnly(URLFetcher):
        def fetch(self, url, headers=None):
            asset = bundled_asset(url)
            if asset is None:
                raise ValueError(f"only bundled assets load in reports: {url[:80]}")
            return super().fetch(asset.as_uri(), headers)

    HTML(string=html, url_fetcher=_BundledAssetsOnly()).write_pdf(str(filepath))


class ReportGenerator:
    def __init__(self, output_dir: str = "data/reports") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def generate_pdf(
        self,
        report: BIReport,
        profile: BusinessProfile,
        company: str = "",
        marketing: MarketingAnalysis | None = None,
        *,
        score: float | None = None,
        source_url: str = "",
    ) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        html = render_bi_html(
            report, profile, marketing, company=company, score=score, source_url=source_url
        )

        safe_name = _sanitize_filename(company or profile.company_name)
        filename = f"{safe_name}_{ts}.pdf"
        filepath = self._output_dir / filename
        write_pdf(html, filepath)
        logger.info("PDF generated: %s", filepath)
        return filepath

    def generate_docx(
        self,
        report: BIReport,
        profile: BusinessProfile,
        company: str = "",
        marketing: MarketingAnalysis | None = None,
    ) -> Path:
        doc = Document()
        style = doc.styles["Normal"]
        style.font.size = Pt(11)
        style.font.name = "Helvetica"

        doc.add_heading("Business Intelligence Report", level=1)
        p = doc.add_paragraph(f"{company or profile.company_name} - Generated {datetime.now().strftime('%B %d, %Y')}")
        p.italic = True

        doc.add_heading("Executive Summary", level=2)
        doc.add_paragraph(report.executive_summary)

        doc.add_heading("1. Business Profile", level=2)
        for label, value in [
            ("Company", profile.company_name),
            ("Tagline", profile.tagline),
            ("Industry", profile.industry),
            ("Value Proposition", profile.value_proposition),
        ]:
            p = doc.add_paragraph()
            run = p.add_run(f"{label}: ")
            run.bold = True
            p.add_run(value)

        doc.add_heading("Offerings", level=3)
        for o in profile.offerings:
            doc.add_paragraph(o, style="List Bullet")

        doc.add_heading("Target Audience", level=3)
        for a in profile.target_audience:
            doc.add_paragraph(a, style="List Bullet")

        doc.add_heading("2. Market Analysis", level=2)
        for label, value in [
            ("Target Market", report.market_analysis.target_market),
            ("Market Size", report.market_analysis.market_size),
            ("Pricing Strategy", report.market_analysis.pricing_strategy),
            ("Market Positioning", report.market_analysis.market_positioning),
        ]:
            p = doc.add_paragraph()
            run = p.add_run(f"{label}: ")
            run.bold = True
            p.add_run(value)

        doc.add_heading("Market Trends", level=3)
        for t in report.market_analysis.market_trends:
            doc.add_paragraph(t, style="List Bullet")

        if report.market_analysis.customer_segments:
            doc.add_heading("Customer Segments", level=3)
            for s in report.market_analysis.customer_segments:
                doc.add_paragraph(s, style="List Bullet")

        doc.add_heading("3. Competitive Analysis", level=2)
        for heading, competitors in [
            ("Direct Competitors", report.competitive_analysis.direct_competitors),
            ("Indirect Competitors", report.competitive_analysis.indirect_competitors),
        ]:
            if not competitors:
                continue
            doc.add_heading(heading, level=3)
            for c in competitors:
                doc.add_paragraph(_competitor_line(c), style="List Bullet")

        doc.add_heading("Competitive Advantages", level=3)
        for a in report.competitive_analysis.competitive_advantages:
            doc.add_paragraph(a, style="List Bullet")

        if report.competitive_analysis.market_gaps:
            doc.add_heading("Market Gaps", level=3)
            for g in report.competitive_analysis.market_gaps:
                doc.add_paragraph(g, style="List Bullet")

        p = doc.add_paragraph()
        run = p.add_run("Threat Assessment: ")
        run.bold = True
        p.add_run(report.competitive_analysis.threat_assessment)

        doc.add_heading("4. SWOT Analysis", level=2)
        for category, items in [
            ("Strengths", report.swot_analysis.strengths),
            ("Weaknesses", report.swot_analysis.weaknesses),
            ("Opportunities", report.swot_analysis.opportunities),
            ("Threats", report.swot_analysis.threats),
        ]:
            doc.add_heading(category, level=3)
            for item in items:
                doc.add_paragraph(item, style="List Bullet")

        doc.add_heading("5. Recommendations", level=2)
        for r in report.recommendations:
            doc.add_paragraph(r, style="List Bullet")

        doc.add_heading("6. Risk Factors", level=2)
        for r in report.risk_factors:
            doc.add_paragraph(r, style="List Bullet")

        if marketing:
            doc.add_heading("7. Target Audience & Personas", level=2)
            for p in marketing.personas:
                doc.add_heading(p.name or "Persona", level=3)
                doc.add_paragraph(p.description)
                if p.pain_points:
                    doc.add_heading("Pain Points", level=4)
                    for pp in p.pain_points:
                        doc.add_paragraph(pp, style="List Bullet")
                if p.goals:
                    doc.add_heading("Goals", level=4)
                    for g in p.goals:
                        doc.add_paragraph(g, style="List Bullet")
                if p.channels:
                    doc.add_heading("Channels", level=4)
                    for c in p.channels:
                        doc.add_paragraph(c, style="List Bullet")

            doc.add_heading("8. Brand Personality", level=2)
            if marketing.brand_personality:
                for label, value in [
                    ("Archetype", marketing.brand_personality.archetype),
                    ("Voice", marketing.brand_personality.voice_characteristics),
                ]:
                    p = doc.add_paragraph()
                    run = p.add_run(f"{label}: ")
                    run.bold = True
                    p.add_run(value)
                doc.add_heading("Traits", level=3)
                for t in marketing.brand_personality.traits:
                    doc.add_paragraph(t, style="List Bullet")

            doc.add_heading("9. People Also Ask", level=2)
            for item in marketing.faq:
                p = doc.add_paragraph()
                run = p.add_run(item.question)
                run.bold = True
                doc.add_paragraph(item.answer)

            doc.add_heading("10. Customer Journey", level=2)
            for stage in marketing.customer_journey:
                doc.add_heading(stage.stage, level=3)
                doc.add_paragraph(stage.description)
                if stage.touchpoints:
                    doc.add_heading("Touchpoints", level=4)
                    for t in stage.touchpoints:
                        doc.add_paragraph(t, style="List Bullet")

            doc.add_heading("11. E-E-A-T & GEO Optimization", level=2)
            if marketing.eeat:
                doc.add_heading("E-E-A-T Signals", level=3)
                for label, items in [
                    ("Experience", marketing.eeat.experience),
                    ("Expertise", marketing.eeat.expertise),
                    ("Authoritativeness", marketing.eeat.authoritativeness),
                    ("Trustworthiness", marketing.eeat.trustworthiness),
                ]:
                    if items:
                        doc.add_heading(label, level=4)
                        for s in items:
                            doc.add_paragraph(s, style="List Bullet")
            if marketing.geo_tactics:
                doc.add_heading("GEO Tactics", level=3)
                for g in marketing.geo_tactics:
                    p = doc.add_paragraph()
                    run = p.add_run(f"{g.tactic}: ")
                    run.bold = True
                    p.add_run(g.description)

            doc.add_heading("12. Call to Action", level=2)
            doc.add_paragraph(marketing.call_to_action)

        safe_name = _sanitize_filename(company or profile.company_name)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{ts}.docx"
        filepath = self._output_dir / filename
        _apply_house_style(doc)
        doc.save(str(filepath))
        logger.info("DOCX generated: %s", filepath)
        return filepath


class MarketResearchReportGenerator:
    def __init__(self, output_dir: str = "reports") -> None:
        self._output_dir = Path(output_dir)

    def _ensure_dir(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _resources_to_docx(self, doc: Document, title: str, resources: list) -> None:
        if not resources:
            return
        doc.add_heading(title, level=2)
        for r in resources:
            p = doc.add_paragraph()
            run = p.add_run(r.name)
            run.bold = True
            if r.url:
                p.add_run(f" - {r.url}")
            doc.add_paragraph(r.description, style="List Bullet")
            if r.relevance:
                doc.add_paragraph(f"Relevance: {r.relevance}", style="List Bullet")

    def generate_pdf(self, result: MarketResearchResult) -> Path:
        self._ensure_dir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        html = render_research_html(result)

        safe_name = _sanitize_filename(result.market_query)
        filename = f"market_research_{safe_name}_{ts}.pdf"
        filepath = self._output_dir / filename
        write_pdf(html, filepath)
        logger.info("Market research PDF generated: %s", filepath)
        return filepath

    def generate_docx(self, result: MarketResearchResult) -> Path:
        self._ensure_dir()
        doc = Document()
        style = doc.styles["Normal"]
        style.font.size = Pt(11)
        style.font.name = "Helvetica"

        doc.add_heading("Market Research Report", level=1)
        p = doc.add_paragraph(f"{result.market_query} - Generated {datetime.now().strftime('%B %d, %Y')}")
        p.italic = True

        doc.add_heading("Executive Summary", level=2)
        doc.add_paragraph(result.executive_summary)

        if result.market_size:
            doc.add_heading("Market Size", level=2)
            doc.add_paragraph(result.market_size)
        if result.growth_rate:
            doc.add_heading("Growth Rate", level=2)
            doc.add_paragraph(result.growth_rate)

        if result.key_insights:
            doc.add_heading("Key Insights", level=2)
            for insight in result.key_insights:
                doc.add_paragraph(insight, style="List Bullet")

        if result.recommendations:
            doc.add_heading("Recommendations", level=2)
            for rec in result.recommendations:
                doc.add_paragraph(rec, style="List Bullet")

        if result.risks_and_assumptions:
            doc.add_heading("Risks & Assumptions", level=2)
            for risk in result.risks_and_assumptions:
                doc.add_paragraph(risk, style="List Bullet")

        doc.add_heading("Market Landscape", level=2)
        doc.add_paragraph(result.summary)

        if result.swot is not None:
            doc.add_heading("Market SWOT", level=2)
            for category, items in [
                ("Strengths", result.swot.strengths),
                ("Weaknesses", result.swot.weaknesses),
                ("Opportunities", result.swot.opportunities),
                ("Threats", result.swot.threats),
            ]:
                if items:
                    doc.add_heading(category, level=3)
                    for item in items:
                        doc.add_paragraph(item, style="List Bullet")

        if result.pestel is not None:
            doc.add_heading("PESTEL Analysis", level=2)
            for label, items in _pestel_rows(result):
                if items:
                    doc.add_heading(label, level=3)
                    for item in items:
                        doc.add_paragraph(item, style="List Bullet")

        if result.five_forces is not None:
            doc.add_heading("Porter's Five Forces", level=2)
            for label, text in _five_forces_rows(result):
                if text:
                    p = doc.add_paragraph()
                    run = p.add_run(f"{label}: ")
                    run.bold = True
                    p.add_run(text)

        self._resources_to_docx(doc, "Key Players", result.key_players)
        self._resources_to_docx(doc, "Competitor Sites", result.competitor_sites)
        self._resources_to_docx(doc, "Industry Websites & Resources", result.industry_websites)
        self._resources_to_docx(doc, "News & Publications", result.news_sources)
        self._resources_to_docx(doc, "Research Papers & Reports", result.research_papers)
        self._resources_to_docx(doc, "Communities & Forums", result.relevant_communities)

        safe_name = _sanitize_filename(result.market_query)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"market_research_{safe_name}_{ts}.docx"
        filepath = self._output_dir / filename
        _apply_house_style(doc)
        doc.save(str(filepath))
        logger.info("Market research DOCX generated: %s", filepath)
        return filepath
