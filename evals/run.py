"""Offline evaluation of the analysis pipeline.

Runs the real agents against saved HTML so scraping never varies between runs,
then scores each stage three ways:

  completeness  the deterministic contract score, same as production
  confidence    the LLM critic, when enabled
  assertions    per-case expectations from cases.yaml

Usage:
    uv run python -m evals.run                    # run everything, write a report
    uv run python -m evals.run --case stripe      # one case
    uv run python -m evals.run --stages profile,market
    uv run python -m evals.run --check            # fail if worse than the baseline
    uv run python -m evals.run --update-baseline

Needs NVIDIA_API_KEY: the agents are real. Only the network fetch is stubbed.
"""

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from bs4 import BeautifulSoup

from app.analysis.critic import build_critic
from app.analysis.runner import AgentRunner, StageContext, StageError
from app.analysis.specs import BI_SPECS
from app.config import settings
from app.models.bi import ScrapedContent
from app.pipeline.contracts import _iter_strings
from app.scraper.engine import ScraperEngine

EVALS_DIR = Path(__file__).parent
FIXTURES = EVALS_DIR / "fixtures"
RESULTS = EVALS_DIR / "results"
BASELINE = EVALS_DIR / "baseline.json"

# A drop larger than this against the baseline fails --check. Loose enough to
# absorb model nondeterminism, tight enough to catch a prompt regression.
REGRESSION_TOLERANCE = 5.0


@dataclass
class StageOutcome:
    stage: str
    ok: bool
    completeness: float | None = None
    confidence: float | None = None
    duration_ms: int = 0
    attempts: int = 0
    warnings: list[str] = field(default_factory=list)
    assertion_failures: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def score(self) -> float:
        """Completeness, less a penalty for each failed case assertion."""
        if not self.ok or self.completeness is None:
            return 0.0
        return max(0.0, self.completeness - 15.0 * len(self.assertion_failures))


@dataclass
class CaseOutcome:
    case: str
    stages: list[StageOutcome] = field(default_factory=list)

    @property
    def mean_score(self) -> float:
        scores = [s.score for s in self.stages]
        return round(statistics.mean(scores), 1) if scores else 0.0


def load_cases() -> list[dict[str, Any]]:
    return list(yaml.safe_load((EVALS_DIR / "cases.yaml").read_text())["cases"])


def scrape_fixture(case: dict[str, Any]) -> ScrapedContent:
    """Parse a saved page with the real scraper, minus the network call.

    Everything after the fetch is production code, so extraction bugs still show
    up here.
    """
    html = (FIXTURES / case["fixture"]).read_text(errors="ignore")
    engine = ScraperEngine()
    soup = BeautifulSoup(html, "lxml")
    title = engine._extract_title(soup)
    description = engine._extract_meta(soup, "description")
    keywords = engine._extract_keywords(soup)
    headings = engine._extract_headings(BeautifulSoup(html, "lxml"))
    text = engine._extract_text(BeautifulSoup(html, "lxml"))
    return ScrapedContent(
        url=case["url"],
        title=title,
        description=description,
        headings=headings,
        text_content=text[:50_000],
        meta_keywords=keywords,
    )


def _value_text(value: Any) -> str:
    """Every string *value* in the model, without the field names.

    Serialising the whole dict would include keys, so a forbidden term like
    "competitor" would always match `direct_competitors` no matter what the
    model actually said - a false failure that says nothing about the output.
    """
    return " ".join(_iter_strings(value.model_dump())).lower()


def check_assertions(stage: str, value: Any, case: dict[str, Any]) -> list[str]:
    """Per-case expectations. Only the profile stage has grounded facts to check."""
    failures: list[str] = []
    blob = _value_text(value)

    for forbidden in case.get("forbidden", []):
        if forbidden.lower() in blob:
            failures.append(f"contains forbidden text {forbidden!r}")

    if stage != "profile":
        return failures

    company = case.get("company", "")
    if company and company.lower() not in str(getattr(value, "company_name", "")).lower():
        failures.append(
            f"company_name {getattr(value, 'company_name', '')!r} does not match {company!r}"
        )

    industry = str(getattr(value, "industry", "")).lower()
    terms = case.get("industry_terms", [])
    if terms and not any(t.lower() in industry for t in terms):
        failures.append(f"industry {industry!r} matches none of {terms}")

    for group in case.get("must_mention", []):
        if not any(term.lower() in blob for term in group):
            failures.append(f"mentions none of {group}")

    minimum = case.get("min_offerings", 0)
    offerings = getattr(value, "offerings", []) or []
    if len(offerings) < minimum:
        failures.append(f"{len(offerings)} offerings, expected at least {minimum}")

    return failures


async def run_case(
    case: dict[str, Any], runner: AgentRunner, stages: list[str]
) -> CaseOutcome:
    outcome = CaseOutcome(case=case["name"])
    ctx = StageContext(url=case["url"], scraped=scrape_fixture(case))

    for spec in BI_SPECS:
        if stages and spec.key not in stages:
            continue
        try:
            result = await runner.run_with_review(spec, ctx)
        except StageError as e:
            outcome.stages.append(StageOutcome(stage=spec.key, ok=False, error=e.message))
            print(f"    {spec.key:12} FAILED  {e.message[:60]}")
            break  # later stages depend on this one

        setattr(ctx, _CTX_FIELD[spec.key], result.value)
        failures = check_assertions(spec.key, result.value, case)
        outcome.stages.append(
            StageOutcome(
                stage=spec.key,
                ok=True,
                completeness=result.metrics.completeness,
                confidence=result.metrics.confidence,
                duration_ms=result.metrics.duration_ms,
                attempts=result.metrics.attempts,
                warnings=result.metrics.warnings,
                assertion_failures=failures,
            )
        )
        flag = "  " if not failures else " !"
        conf = f" critic {result.metrics.confidence:.0f}" if result.metrics.confidence else ""
        print(
            f"    {spec.key:12}{flag} score {outcome.stages[-1].score:5.1f}"
            f"  complete {result.metrics.completeness:5.1f}{conf}"
            f"  {result.metrics.duration_ms / 1000:.1f}s"
        )
        for failure in failures:
            print(f"                 - {failure}")

    return outcome


_CTX_FIELD = {
    "profile": "profile",
    "market": "market",
    "competitive": "competitive",
    "swot": "swot",
    "marketing": "marketing",
    "report": "report",
}


def render_markdown(outcomes: list[CaseOutcome], stages: list[str]) -> str:
    columns = stages or [s.key for s in BI_SPECS]
    lines = [
        "# Eval results",
        "",
        f"Cases: {len(outcomes)} · stages: {', '.join(columns)}",
        "",
        "| Case | " + " | ".join(columns) + " | mean |",
        "|" + "---|" * (len(columns) + 2),
    ]
    for outcome in outcomes:
        by_stage = {s.stage: s for s in outcome.stages}
        cells = []
        for stage in columns:
            s = by_stage.get(stage)
            if s is None:
                cells.append("–")
            elif not s.ok:
                cells.append("**fail**")
            else:
                cells.append(f"{s.score:.0f}" + ("!" if s.assertion_failures else ""))
        lines.append(f"| {outcome.case} | " + " | ".join(cells) + f" | {outcome.mean_score:.1f} |")

    overall = statistics.mean([o.mean_score for o in outcomes]) if outcomes else 0.0
    lines += ["", f"**Overall: {overall:.1f}**", "", "`!` marks a failed case assertion.", ""]

    problems = [
        (o.case, s)
        for o in outcomes
        for s in o.stages
        if s.assertion_failures or not s.ok or s.warnings
    ]
    if problems:
        lines += ["## Findings", ""]
        for case_name, s in problems:
            lines.append(f"**{case_name} · {s.stage}**")
            if s.error:
                lines.append(f"- failed: {s.error}")
            for failure in s.assertion_failures:
                lines.append(f"- assertion: {failure}")
            for warning in s.warnings[:4]:
                lines.append(f"- contract: {warning}")
            lines.append("")
    return "\n".join(lines)


def summarise(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    per_stage: dict[str, list[float]] = {}
    for outcome in outcomes:
        for s in outcome.stages:
            per_stage.setdefault(s.stage, []).append(s.score)
    return {
        "overall": round(statistics.mean([o.mean_score for o in outcomes]), 1)
        if outcomes
        else 0.0,
        "per_stage": {k: round(statistics.mean(v), 1) for k, v in per_stage.items()},
        "per_case": {o.case: o.mean_score for o in outcomes},
    }


def compare_to_baseline(summary: dict[str, Any]) -> int:
    if not BASELINE.exists():
        print("\nNo baseline recorded. Write one with --update-baseline.")
        return 1

    baseline = json.loads(BASELINE.read_text())
    failed = False
    print("\nAgainst baseline:")

    delta = summary["overall"] - baseline["overall"]
    verdict = "ok" if delta >= -REGRESSION_TOLERANCE else "REGRESSION"
    print(f"  overall {baseline['overall']:.1f} -> {summary['overall']:.1f} ({delta:+.1f}) {verdict}")
    failed |= delta < -REGRESSION_TOLERANCE

    for stage, score in summary["per_stage"].items():
        before = baseline["per_stage"].get(stage)
        if before is None:
            print(f"  {stage:12} new stage, {score:.1f}")
            continue
        d = score - before
        mark = "ok" if d >= -REGRESSION_TOLERANCE else "REGRESSION"
        print(f"  {stage:12} {before:.1f} -> {score:.1f} ({d:+.1f}) {mark}")
        failed |= d < -REGRESSION_TOLERANCE

    return 1 if failed else 0


async def main_async(args: argparse.Namespace) -> int:
    if not settings.nvidia_api_key:
        print("NVIDIA_API_KEY is not set - the agents are real, so this cannot run.")
        return 2

    cases = load_cases()
    if args.case:
        wanted = set(args.case.split(","))
        cases = [c for c in cases if c["name"] in wanted]
        if not cases:
            print(f"No case matched {args.case!r}")
            return 2

    stages = [s.strip() for s in args.stages.split(",")] if args.stages else []

    if args.no_critic:
        settings.enable_stage_critic = False
    runner = AgentRunner(
        critic=build_critic(), critic_retry_threshold=settings.critic_retry_threshold
    )

    print(f"Running {len(cases)} case(s), critic={'on' if settings.enable_stage_critic else 'off'}\n")
    outcomes = []
    for case in cases:
        print(f"  {case['name']}  ({case['url']})")
        outcomes.append(await run_case(case, runner, stages))
        print()

    summary = summarise(outcomes)
    report = render_markdown(outcomes, stages)
    print(report)

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = args.label or "latest"
    (RESULTS / f"{stamp}.md").write_text(report)
    (RESULTS / f"{stamp}.json").write_text(
        json.dumps(
            {"summary": summary, "cases": [asdict(o) for o in outcomes]}, indent=2
        )
    )
    print(f"Wrote {RESULTS / f'{stamp}.md'}")

    if args.update_baseline:
        BASELINE.write_text(json.dumps(summary, indent=2))
        print(f"Baseline updated: overall {summary['overall']:.1f}")
        return 0

    if args.check:
        return compare_to_baseline(summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="comma-separated case names")
    parser.add_argument("--stages", help="comma-separated stage slugs")
    parser.add_argument("--check", action="store_true", help="fail on regression vs baseline")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--no-critic", action="store_true", help="skip the LLM review pass")
    parser.add_argument("--label", help="name for the results files")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
