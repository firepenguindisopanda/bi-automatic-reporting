"""Message bodies.

Branded with the same editorial tokens as the app and the PDF (app/branding.py):
an ink masthead with the rust rubric, warm paper, serif text, hairline rules.
Mail clients strip most CSS and block remote images, so the layout is tables
and inline styles with no images at all; the one <style> block only stacks the
figures on narrow screens, and clients that drop it still get a working layout.

Everything about the report in the message - company name, summary,
recommendations - is model output derived from whatever page the submitter
pointed at: attacker-controlled, in effect. It goes through
``display_company_name`` or ``display_text`` (control characters out, explicit
links out, length capped) before it reaches a header or a body, and is
HTML-escaped in the HTML part.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date

from app.branding import PALETTE as C
from app.branding import brand, display_host, without_em_dashes
from app.models.bi import BIReport, BusinessProfile, MarketingAnalysis

MAX_COMPANY_NAME_CHARS = 80
FALLBACK_COMPANY_NAME = "your business"
MAX_SUMMARY_CHARS = 480
MAX_RECOMMENDATION_CHARS = 240
RECOMMENDATIONS_SHOWN = 3

SERIF = "'Source Serif 4',Georgia,'Times New Roman',serif"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
# Explicit links only. A bare domain is left alone because real trading names
# look like one - "Fly.io", "Booking.com" - and mangling those helps nobody.
_LINKS = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)\S*", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?](?=\s)")


def _clean(text: str | None) -> str:
    text = _CONTROL_CHARS.sub(" ", text or "")
    text = _LINKS.sub(" ", text)
    return without_em_dashes(" ".join(text.split()))


def _clip(text: str, limit: int) -> str:
    """Cut at a sentence end if one is close enough, else at a word, with an ellipsis."""
    if len(text) <= limit:
        return text
    window = text[: limit - 1]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends and ends[-1] >= limit * 0.6:
        return window[: ends[-1]]
    return window.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def display_company_name(name: str | None) -> str:
    """A company name that is safe to put in a subject line and a body.

    Strips control characters (a newline in a subject is header injection),
    drops explicit links so the message cannot be turned into a phishing lure,
    collapses whitespace and caps the length.
    """
    return _clip(_clean(name), MAX_COMPANY_NAME_CHARS) or FALLBACK_COMPANY_NAME


def display_text(text: str | None, limit: int) -> str:
    """Model prose made safe to quote in a message: same rules as the name, longer cap."""
    return _clip(_clean(text), limit)


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text or ""):
        if block.strip():
            return block
    return ""


@dataclass(frozen=True, slots=True)
class ReportDigest:
    """What the message says about one report."""

    company_name: str
    summary: str = ""
    recommendations: tuple[str, ...] = ()
    competitors: int = 0
    risks: int = 0
    score: float | None = None
    source_url: str = ""
    report_url: str | None = None
    has_marketing: bool = False
    generated: date = field(default_factory=date.today)

    @classmethod
    def from_report(
        cls,
        report: BIReport,
        profile: BusinessProfile,
        marketing: MarketingAnalysis | None = None,
        *,
        source_url: str = "",
        score: float | None = None,
        report_url: str | None = None,
    ) -> "ReportDigest":
        competitive = report.competitive_analysis
        return cls(
            company_name=profile.company_name,
            summary=_first_paragraph(report.executive_summary),
            recommendations=tuple(r for r in report.recommendations if r.strip()),
            competitors=len(competitive.direct_competitors) + len(competitive.indirect_competitors),
            risks=sum(1 for r in report.risk_factors if r.strip()),
            score=score,
            source_url=source_url or profile.website,
            report_url=report_url,
            has_marketing=marketing is not None,
        )

    @property
    def score_text(self) -> str | None:
        return None if self.score is None else f"{round(self.score)}"

    @property
    def host(self) -> str:
        return display_host(self.source_url)


def _contents(digest: ReportDigest) -> list[str]:
    sections = [
        "Executive summary",
        "Business profile",
        "Market and sizing",
        "Competition",
        "SWOT analysis",
        "Recommendations and risks",
    ]
    if digest.has_marketing:
        sections += ["Personas and brand voice", "Journey, FAQs and AI visibility"]
    return sections


def report_subject(company_name: str) -> str:
    name = " ".join(_CONTROL_CHARS.sub(" ", brand().name).split())
    return f"Your {name} report on {display_company_name(company_name)} is ready"


# --- HTML ------------------------------------------------------------------

def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _eyebrow(text: str, colour: str = C["ink_muted"]) -> str:
    return (
        f'<p style="margin:0 0 12px 0;font-family:{SANS};font-size:11px;line-height:16px;'
        f'font-weight:600;letter-spacing:0.14em;text-transform:uppercase;color:{colour};">'
        f"{_e(text)}</p>"
    )


def _masthead(kind: str) -> str:
    b = brand()
    return f"""\
<tr><td class="px" style="background:{C['ink']};padding:24px 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="border-left:3px solid {C['accent_bright']};padding:0 0 0 12px;font-family:{SERIF};
               font-size:24px;line-height:26px;color:{C['on_ink']};letter-spacing:-0.01em;">{_e(b.name)}</td>
    <td align="right" style="font-family:{SANS};font-size:10px;line-height:14px;font-weight:600;
               letter-spacing:0.16em;text-transform:uppercase;color:{C['on_ink_muted']};">{_e(kind)}</td>
  </tr></table>
</td></tr>
<tr><td style="height:3px;line-height:3px;font-size:0;background:{C['accent']};">&nbsp;</td></tr>"""


def _footer(digest: ReportDigest | None) -> str:
    b = brand()
    links = []
    if b.author_email:
        links.append(
            f'<a href="mailto:{_e(b.author_email)}" style="color:{C["on_ink_accent"]};'
            f'text-decoration:none;">{_e(b.author_email)}</a>'
        )
    if b.author_url:
        links.append(
            f'<a href="{_e(b.author_url)}" style="color:{C["on_ink_accent"]};'
            f'text-decoration:none;">{_e(b.author_url_label)}</a>'
        )
    byline = (
        f'<p style="margin:14px 0 0 0;font-family:{SERIF};font-size:15px;line-height:22px;'
        f'color:{C["on_ink"]};">Made by {_e(b.author)}</p>'
        if b.author else ""
    )
    link_line = (
        f'<p style="margin:4px 0 0 0;font-family:{SANS};font-size:13px;line-height:20px;">'
        + f' <span style="color:{C["on_ink_muted"]};">&middot;</span> '.join(links)
        + "</p>"
        if links else ""
    )
    source = (
        f"Generated automatically from the public pages of {_e(digest.host)}. "
        if digest and digest.host else ""
    )
    fine = (
        f'<p style="margin:22px 0 0 0;padding-top:18px;border-top:1px solid {C["on_ink_rule"]};'
        f'font-family:{SANS};font-size:11px;line-height:17px;color:{C["on_ink_muted"]};">'
        f"{source}AI analysis can be wrong, so check figures before acting on them. "
        "You received this because this address was entered when the report was requested.</p>"
        if digest else ""
    )
    tagline = (
        f'<span style="font-family:{SANS};font-size:12px;color:{C["on_ink_muted"]};">'
        f"&nbsp;&nbsp;{_e(b.tagline)}</span>"
        if b.tagline else ""
    )
    return f"""\
<tr><td class="px" style="background:{C['ink']};padding:30px 40px 32px 40px;">
  <p style="margin:0;font-family:{SERIF};font-size:18px;line-height:24px;color:{C['on_ink']};">
    <span style="border-left:3px solid {C['accent_bright']};padding-left:9px;">{_e(b.name)}</span>{tagline}
  </p>
  {byline}
  {link_line}
  {fine}
</td></tr>"""


def _page(title: str, preheader: str, rows: str) -> str:
    # The run of zero-width joiners stops clients filling the preview line
    # with the first words of the body after the preheader runs out.
    filler = "&#8199;&#847;" * 60
    return f"""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{_e(title)}</title>
<style>
  @media only screen and (max-width: 620px) {{
    .px {{ padding-left: 24px !important; padding-right: 24px !important; }}
    .stat {{ display: block !important; width: 100% !important; border-left: 0 !important;
             padding: 12px 0 !important; }}
    .stat + .stat {{ border-top: 1px solid {C['rule']} !important; }}
    .h1 {{ font-size: 34px !important; line-height: 40px !important; }}
    .col {{ display: block !important; width: 100% !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{C['frame']};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;mso-hide:all;
            font-size:1px;line-height:1px;color:{C['frame']};">{_e(preheader)}{filler}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{C['frame']};">
<tr><td align="center" style="padding:32px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="width:100%;max-width:600px;background:{C['surface']};border:1px solid {C['rule']};">
{rows}
</table>
</td></tr>
</table>
</body>
</html>"""


def _stat(label: str, value: str, unit: str = "", *, first: bool = False, accent: bool = False) -> str:
    border = "" if first else f"border-left:1px solid {C['rule']};"
    pad = "padding:18px 12px 18px 0;" if first else "padding:18px 12px 18px 20px;"
    colour = C["accent"] if accent else C["ink"]
    unit_html = (
        f'<span style="font-family:{SANS};font-size:12px;color:{C["ink_faint"]};">&nbsp;{_e(unit)}</span>'
        if unit else ""
    )
    return f"""\
<td class="stat" valign="top" style="{pad}{border}">
  <div style="font-family:{SANS};font-size:10px;line-height:14px;font-weight:600;letter-spacing:0.12em;
              text-transform:uppercase;color:{C['ink_muted']};">{_e(label)}</div>
  <div style="font-family:{SERIF};font-size:32px;line-height:38px;color:{colour};
              padding-top:4px;">{_e(value)}{unit_html}</div>
</td>"""


def report_html(digest: ReportDigest) -> str:
    company = display_company_name(digest.company_name)
    summary = display_text(digest.summary, MAX_SUMMARY_CHARS)
    shown = [
        display_text(r, MAX_RECOMMENDATION_CHARS)
        for r in digest.recommendations[:RECOMMENDATIONS_SHOWN]
    ]
    shown = [r for r in shown if r]
    more = len(digest.recommendations) - len(shown)
    when = f"{digest.generated.day} {digest.generated:%B %Y}"

    lede = "Your report is ready."
    if digest.host:
        lede += f" We read {_e(digest.host)} closely and wrote up what we found."
    lede += " The full report is attached as a PDF and a Word document."

    stats = []
    if digest.score_text is not None:
        stats.append(_stat("Quality score", digest.score_text, "/ 100", first=True, accent=True))
    stats.append(_stat("Competitors", str(digest.competitors), first=not stats))
    stats.append(_stat("Recommendations", str(len(digest.recommendations)), first=False))

    rows = [_masthead("Business intelligence")]
    rows.append(f"""\
<tr><td class="px" style="padding:40px 40px 0 40px;">
  {_eyebrow(f"Strategic report · {when}", C['accent'])}
  <h1 class="h1" style="margin:0;font-family:{SERIF};font-size:42px;line-height:48px;font-weight:400;
             letter-spacing:-0.02em;color:{C['ink']};">{_e(company)}</h1>
  <p style="margin:16px 0 0 0;font-family:{SERIF};font-size:18px;line-height:28px;color:{C['ink_muted']};">{lede}</p>
</td></tr>
<tr><td class="px" style="padding:28px 40px 0 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="border-top:2px solid {C['ink']};border-bottom:1px solid {C['rule']};">
    <tr>{"".join(stats)}</tr>
  </table>
</td></tr>""")

    if summary:
        rows.append(f"""\
<tr><td class="px" style="padding:32px 40px 0 40px;">
  {_eyebrow("The short version")}
  <p style="margin:0;font-family:{SERIF};font-size:17px;line-height:28px;color:{C['ink']};">{_e(summary)}</p>
</td></tr>""")

    if shown:
        items = "".join(
            f"""\
<tr>
  <td width="52" valign="top" style="padding:16px 0;border-top:1px solid {C['rule']};font-family:{SERIF};
             font-size:24px;line-height:26px;color:{C['accent']};">{i:02d}</td>
  <td valign="top" style="padding:16px 0;border-top:1px solid {C['rule']};font-family:{SERIF};
             font-size:16px;line-height:25px;color:{C['ink']};">{_e(text)}</td>
</tr>"""
            for i, text in enumerate(shown, start=1)
        )
        more_line = (
            f'<p style="margin:4px 0 0 52px;font-family:{SANS};font-size:13px;line-height:20px;'
            f'color:{C["ink_muted"]};">Plus {more} more in the report.</p>'
            if more > 0 else ""
        )
        rows.append(f"""\
<tr><td class="px" style="padding:32px 40px 0 40px;">
  {_eyebrow("Where to start")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{items}</table>
  {more_line}
</td></tr>""")

    if digest.report_url:
        rows.append(f"""\
<tr><td class="px" style="padding:32px 40px 0 40px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="background:{C['ink']};border-radius:4px;">
      <a href="{_e(digest.report_url)}" style="display:inline-block;padding:15px 28px;font-family:{SANS};
         font-size:15px;line-height:18px;font-weight:600;color:{C['on_ink']};text-decoration:none;
         border-radius:4px;">View the report online &rarr;</a>
    </td>
  </tr></table>
</td></tr>""")

    contents = _contents(digest)
    half = (len(contents) + 1) // 2

    def column(items: list[str], start: int) -> str:
        return "".join(
            f'<p style="margin:0 0 8px 0;font-family:{SERIF};font-size:15px;line-height:22px;color:{C["ink"]};">'
            f'<span style="display:inline-block;width:28px;font-family:{SANS};font-size:11px;font-weight:600;'
            f'color:{C["accent"]};">{n:02d}</span>{_e(item)}</p>'
            for n, item in enumerate(items, start=start)
        )

    rows.append(f"""\
<tr><td class="px" style="padding:36px 40px 40px 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{C['sunk']};border-radius:6px;">
    <tr><td style="padding:22px 24px 14px 24px;">
      {_eyebrow("Inside the report")}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="col" width="50%" valign="top">{column(contents[:half], 1)}</td>
        <td class="col" width="50%" valign="top">{column(contents[half:], half + 1)}</td>
      </tr></table>
    </td></tr>
  </table>
</td></tr>""")
    rows.append(_footer(digest))

    stat_bits = []
    if digest.score_text is not None:
        stat_bits.append(f"Quality score {digest.score_text}/100")
    stat_bits.append(f"{len(digest.recommendations)} recommendations")
    preheader = " · ".join(stat_bits) + " · market, competition and a full SWOT inside."
    return _page(report_subject(digest.company_name), preheader, "\n".join(rows))


# --- Plain text -------------------------------------------------------------

def report_text(digest: ReportDigest) -> str:
    """Plain-text alternative. Its absence hurts deliverability."""
    b = brand()
    company = display_company_name(digest.company_name)
    lines = [f"{b.name.upper()} · Business intelligence", "", f"Your report on {company} is ready."]
    figures = []
    if digest.score_text is not None:
        figures.append(f"Quality score {digest.score_text}/100")
    figures += [f"{digest.competitors} competitors", f"{len(digest.recommendations)} recommendations"]
    lines += [" · ".join(figures), ""]

    summary = display_text(digest.summary, MAX_SUMMARY_CHARS)
    if summary:
        lines += ["THE SHORT VERSION", summary, ""]

    shown = [display_text(r, MAX_RECOMMENDATION_CHARS) for r in digest.recommendations[:RECOMMENDATIONS_SHOWN]]
    shown = [r for r in shown if r]
    if shown:
        lines.append("WHERE TO START")
        lines += [f"{i}. {text}" for i, text in enumerate(shown, start=1)]
        if len(digest.recommendations) > len(shown):
            lines.append(f"Plus {len(digest.recommendations) - len(shown)} more in the report.")
        lines.append("")

    if digest.report_url:
        lines += [f"View the report online: {digest.report_url}", ""]
    lines += ["The full report is attached as a PDF and a Word document.", "", "-- "]
    lines.append(f"{b.name}. {b.tagline}".strip() if b.tagline else b.name)
    if b.author:
        lines.append(f"Made by {b.author}")
    contact = " · ".join(x for x in (b.author_email, b.author_url) if x)
    if contact:
        lines.append(contact)
    lines.append("")
    if digest.host:
        lines.append(f"Generated automatically from the public pages of {digest.host}.")
    lines.append("AI analysis can be wrong, so check figures before acting on them.")
    return "\n".join(lines) + "\n"


# --- Selftest ---------------------------------------------------------------

def selftest_subject() -> str:
    return f"{brand().name}: email configuration test"


def selftest_html(provider: str) -> str:
    rows = _masthead("Configuration test") + f"""
<tr><td class="px" style="padding:40px 40px 40px 40px;">
  <h1 style="margin:0 0 14px 0;font-family:{SERIF};font-size:30px;line-height:36px;font-weight:400;color:{C['ink']};">
    Email is configured correctly</h1>
  <p style="margin:0;font-family:{SERIF};font-size:17px;line-height:27px;color:{C['ink_muted']};">
    This message was delivered through the
    <strong style="color:{C['accent']};font-weight:600;">{_e(provider)}</strong> provider.</p>
</td></tr>
""" + _footer(None)
    return _page(selftest_subject(), "Email delivery works.", rows)


def selftest_text(provider: str) -> str:
    return f"Email is configured correctly. Delivered through the {provider} provider.\n"
