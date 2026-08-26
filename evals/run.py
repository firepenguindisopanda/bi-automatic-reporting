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
    uv run python -m evals.run --suite market     # macro -> sizing -> resources
    uv run python -m evals.run --check            # fail on a completeness or
                                                  # confidence regression
    uv run python -m evals.run --update-baseline

Needs NVIDIA_API_KEY: the agents are real. Only the network fetch is stubbed.
"""

import argparse
import asyncio
import json
import statistics
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from bs4 import BeautifulSoup

from app.analysis.critic import build_critic
from app.analysis.runner import AgentRunner, AgentSpec, StageContext, StageError
from app.analysis.specs import BI_SPECS, MARKET_RESEARCH_SPECS
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

# Confidence needs its own, wider band. The two metrics are not equally stable,
# and holding them to one number makes the noisier of them useless as a gate.
#
# Measured 2026-08-26 by running the full BI suite twice with no code change
# between the runs (both saved under tasks/evidence/):
#
#   completeness  per-stage mean moved  0.0 to 10.7   2 of 6 stages over 5.0
#   confidence    per-stage mean moved  6.6 to 13.7   5 of 5 stages over 5.0
#
# At cell level confidence is worse still: mean absolute move 15.0, max 63.0,
# with 65% of cells over 5.0. A gate at 5.0 therefore fires on every run
# regardless of the change under test, which is no gate at all - the noisy
# cousin of the dead metric this harness exists to expose.
#
# 20.0 sits above the largest per-stage move observed (13.7) with room for a
# worse day, and still catches the kind of regression worth catching: a stage
# losing its grounding drops much further than 20 points. Widen it only with a
# fresh measurement attached, and narrow it if the harness ever averages several
# runs, which would shrink the noise this number is paying for.
CONFIDENCE_TOLERANCE = 20.0


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

    @property
    def mean_confidence(self) -> float | None:
        """None, not 0.0 - a stage the critic never scored is absent, not bad."""
        scores = [s.confidence for s in self.stages if s.confidence is not None]
        return round(statistics.mean(scores), 1) if scores else None


@dataclass(frozen=True)
class Suite:
    """One family of cases and the specs that run against them.

    The two suites differ in what a case *is* - a saved HTML page versus a
    market query - and in which specs consume it. Everything downstream
    (scoring, rendering, metric health, baselines) is shared, so a market run
    is reported exactly like a BI one rather than through a second code path.
    """

    name: str
    specs: tuple[AgentSpec[Any], ...]
    cases_file: str
    baseline_file: str
    subject_key: str
    """The case field naming what is under test, shown next to the case name."""
    ctx_field: dict[str, str]
    """Stage key -> StageContext attribute the result feeds. A terminal stage
    nothing else consumes is simply absent."""
    build_context: Callable[[dict[str, Any]], StageContext]

    @property
    def stage_keys(self) -> list[str]:
        return [s.key for s in self.specs]


BI_SUITE = Suite(
    name="bi",
    specs=BI_SPECS,
    cases_file="cases.yaml",
    baseline_file="baseline.json",
    subject_key="url",
    ctx_field={
        "profile": "profile",
        "market": "market",
        "competitive": "competitive",
        "swot": "swot",
        "marketing": "marketing",
        "report": "report",
    },
    build_context=lambda case: StageContext(url=case["url"], scraped=scrape_fixture(case)),
)

MARKET_SUITE = Suite(
    name="market",
    specs=MARKET_RESEARCH_SPECS,
    cases_file="market_cases.yaml",
    baseline_file="market-baseline.json",
    subject_key="market_query",
    # `resources` is terminal - nothing downstream reads it - so it has no entry.
    ctx_field={"macro": "macro", "sizing": "sizing"},
    build_context=lambda case: StageContext(market_query=case["market_query"]),
)

SUITES = {s.name: s for s in (BI_SUITE, MARKET_SUITE)}


def load_cases(suite: Suite = BI_SUITE) -> list[dict[str, Any]]:
    return list(yaml.safe_load((EVALS_DIR / suite.cases_file).read_text())["cases"])


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
    case: dict[str, Any],
    runner: AgentRunner,
    stages: list[str],
    suite: Suite = BI_SUITE,
) -> CaseOutcome:
    outcome = CaseOutcome(case=case["name"])
    ctx = suite.build_context(case)

    for spec in suite.specs:
        if stages and spec.key not in stages:
            continue
        try:
            result = await runner.run_with_review(spec, ctx)
        except StageError as e:
            outcome.stages.append(StageOutcome(stage=spec.key, ok=False, error=e.message))
            print(f"    {spec.key:12} FAILED  {e.message[:60]}")
            break  # later stages depend on this one

        ctx_field = suite.ctx_field.get(spec.key)
        if ctx_field:
            setattr(ctx, ctx_field, result.value)
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


def _health_line(label: str, values: list[float]) -> str:
    """One line saying whether a metric can still tell two runs apart."""
    if not values:
        return f"- **{label}**: not recorded"
    health = _health(values)
    plural = "" if health["distinct"] == 1 else "s"
    note = ""
    if health["dead"]:
        note = " — **DEAD**: one value everywhere, it cannot rank runs"
    elif health["n"] >= 10 and health["distinct"] <= 2:
        note = " — **near-dead**: two values across many stages"
    return (
        f"- **{label}**: {health['n']} stages, {health['distinct']} distinct value{plural}, "
        f"mean {health['mean']}{note}"
    )


def _row(case: str, cells: list[str], mean_cell: str) -> str:
    return f"| {case} | " + " | ".join(cells) + f" | {mean_cell} |"


def render_markdown(outcomes: list[CaseOutcome], stages: list[str]) -> str:
    """One renderer for both suites - `stages` is already resolved by the caller."""
    columns = stages or BI_SUITE.stage_keys
    header = ["| Case | " + " | ".join(columns) + " | mean |", "|" + "---|" * (len(columns) + 2)]

    def contract_cell(s: StageOutcome | None) -> str:
        if s is None:
            return "–"
        if not s.ok:
            return "**fail**"
        return f"{s.score:.0f}" + ("!" if s.assertion_failures else "")

    def confidence_cell(s: StageOutcome | None) -> str:
        if s is None or not s.ok or s.confidence is None:
            return "–"
        return f"{s.confidence:.0f}"

    lines = [
        "# Eval results",
        "",
        f"Cases: {len(outcomes)} · stages: {', '.join(columns)}",
        "",
        "## Contract score",
        "",
        "Deterministic: completeness, less 15 per failed case assertion.",
        "",
        *header,
    ]
    for outcome in outcomes:
        by_stage = {s.stage: s for s in outcome.stages}
        cells = [contract_cell(by_stage.get(stage)) for stage in columns]
        lines.append(_row(outcome.case, cells, f"{outcome.mean_score:.1f}"))

    overall = statistics.mean([o.mean_score for o in outcomes]) if outcomes else 0.0
    lines += ["", f"**Overall: {overall:.1f}**", "", "`!` marks a failed case assertion.", ""]

    # Deliberately a separate table. The contract score is deterministic and the
    # critic score is a noisy small-model judgement; averaging them into one
    # number would hide which of the two actually moved.
    lines += [
        "## Critic confidence",
        "",
        "LLM review, 0-100. Noisy - read the spread, not a single cell.",
        "",
        *header,
    ]
    for outcome in outcomes:
        by_stage = {s.stage: s for s in outcome.stages}
        cells = [confidence_cell(by_stage.get(stage)) for stage in columns]
        mean_conf = outcome.mean_confidence
        lines.append(_row(outcome.case, cells, "–" if mean_conf is None else f"{mean_conf:.1f}"))
    lines.append("")

    # The point of this section: completeness sat at 100 for 42 straight stages
    # and no report ever said so. A metric that cannot vary cannot be evidence.
    per_stage_conf = _collect(outcomes, "confidence")
    flat_completeness = [v for vals in _collect(outcomes, "completeness").values() for v in vals]
    flat_confidence = [v for vals in per_stage_conf.values() for v in vals]
    lines += [
        "## Metric health",
        "",
        _health_line("completeness", flat_completeness),
        _health_line("confidence", flat_confidence),
        "",
    ]

    if per_stage_conf:
        lines += ["Mean confidence by stage, worst first:", ""]
        ranked = sorted(per_stage_conf.items(), key=lambda kv: statistics.mean(kv[1]))
        for stage, vals in ranked:
            lines.append(f"- `{stage}` {statistics.mean(vals):.1f} (n={len(vals)})")
        lines.append("")

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


def _collect(outcomes: list[CaseOutcome], attr: str) -> dict[str, list[float]]:
    """Per-stage values of one metric, skipping stages that never recorded it."""
    out: dict[str, list[float]] = {}
    for outcome in outcomes:
        for s in outcome.stages:
            value = getattr(s, attr)
            if value is not None:
                out.setdefault(s.stage, []).append(value)
    return out


def _health(values: list[float]) -> dict[str, Any]:
    """How much signal a metric still carries.

    ``distinct`` is the number that matters. A metric with one distinct value
    across every stage has stopped measuring, and nothing else in the report
    makes that visible.
    """
    return {
        "n": len(values),
        "distinct": len(set(values)),
        "mean": round(statistics.mean(values), 1) if values else None,
        "dead": len(values) >= 5 and len(set(values)) <= 1,
    }


def summarise(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    per_stage: dict[str, list[float]] = {}
    for outcome in outcomes:
        for s in outcome.stages:
            per_stage.setdefault(s.stage, []).append(s.score)
    per_stage_confidence = _collect(outcomes, "confidence")

    flat_completeness = [v for vals in _collect(outcomes, "completeness").values() for v in vals]
    flat_confidence = [v for vals in per_stage_confidence.values() for v in vals]

    return {
        "overall": round(statistics.mean([o.mean_score for o in outcomes]), 1)
        if outcomes
        else 0.0,
        "per_stage": {k: round(statistics.mean(v), 1) for k, v in per_stage.items()},
        "per_stage_confidence": {
            k: round(statistics.mean(v), 1) for k, v in per_stage_confidence.items()
        },
        "per_case": {o.case: o.mean_score for o in outcomes},
        "metric_health": {
            "completeness": _health(flat_completeness),
            "confidence": _health(flat_confidence),
        },
    }


def compare_to_baseline(summary: dict[str, Any], path: Path | None = None) -> int:
    """Compare against a suite's baseline. Defaults to the BI one."""
    path = path or BASELINE
    if not path.exists():
        print(f"\nNo baseline recorded at {path.name}. Write one with --update-baseline.")
        return 1

    baseline = json.loads(path.read_text())
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

    regressed = check_confidence_drift(summary, baseline)
    failed |= bool(regressed)
    return 1 if failed else 0


def check_confidence_drift(
    summary: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    """Per-stage confidence deltas. Returns the stages that regressed.

    Enforced since M2. Before that the critic re-scored nothing - a re-run was
    kept whenever completeness tied, which it always did - so a confidence drop
    carried no information and this only warned. Now the score reflects an
    arbitration that actually happened, which is what earns it the right to fail
    a build. That was SPEC.md open question 1.

    Two things deliberately do not fail:

    - a stage absent from the baseline, which is new rather than worse
    - a baseline with no confidence recorded at all, which needs one deliberate
      re-baseline first (SPEC.md section 9 lists that under "ask first")

    A stage the critic never scored never reaches here: ``summarise`` builds
    ``per_stage_confidence`` from ``_collect``, which skips ``None``. Absent is
    not the same as zero - ``report`` carries ``critic=False`` and would
    otherwise look like the worst stage in the run.
    """
    current = summary.get("per_stage_confidence") or {}
    if not current:
        return []
    recorded = baseline.get("per_stage_confidence") or {}
    if not recorded:
        print("\n  confidence: not in the baseline yet - re-baseline to start tracking")
        return []

    print("\n  confidence:")
    regressed: list[str] = []
    for stage, score in current.items():
        before = recorded.get(stage)
        if before is None:
            print(f"    {stage:12} new, {score:.1f}")
            continue
        delta = score - before
        mark = "ok" if delta >= -CONFIDENCE_TOLERANCE else "REGRESSION"
        print(f"    {stage:12} {before:.1f} -> {score:.1f} ({delta:+.1f}) {mark}")
        if delta < -CONFIDENCE_TOLERANCE:
            regressed.append(stage)
    if regressed:
        print(f"    {len(regressed)} stage(s) regressed beyond {CONFIDENCE_TOLERANCE:.1f}")
    return regressed


def render_stored(path: Path) -> int:
    """Re-render a finished run through the current renderer.

    This is how a renderer change is verified: the numbers must come out the
    same as when the run happened, and it needs no API key to check.
    """
    if not path.exists():
        print(f"No such results file: {path}")
        return 2

    data = json.loads(path.read_text())
    outcomes = [
        CaseOutcome(
            case=case["case"], stages=[StageOutcome(**stage) for stage in case["stages"]]
        )
        for case in data["cases"]
    ]

    seen: list[str] = []
    for outcome in outcomes:
        for stage in outcome.stages:
            if stage.stage not in seen:
                seen.append(stage.stage)

    print(render_markdown(outcomes, seen))
    return 0


async def main_async(args: argparse.Namespace) -> int:
    if not settings.nvidia_api_key:
        print("NVIDIA_API_KEY is not set - the agents are real, so this cannot run.")
        return 2

    suite = SUITES[args.suite]
    cases = load_cases(suite)
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

    print(
        f"Running {len(cases)} {suite.name} case(s), "
        f"critic={'on' if settings.enable_stage_critic else 'off'}\n"
    )
    outcomes = []
    for case in cases:
        print(f"  {case['name']}  ({case.get(suite.subject_key, '')})")
        outcomes.append(await run_case(case, runner, stages, suite))
        print()

    summary = summarise(outcomes)
    columns = stages or suite.stage_keys
    report = render_markdown(outcomes, columns)
    print(report)

    RESULTS.mkdir(parents=True, exist_ok=True)
    # Suite-scoped so a market run cannot clobber `latest.json`, which is the
    # BI run the M1 regression test reads.
    default_stamp = "latest" if suite is BI_SUITE else f"{suite.name}-latest"
    stamp = args.label or default_stamp
    (RESULTS / f"{stamp}.md").write_text(report)
    (RESULTS / f"{stamp}.json").write_text(
        json.dumps(
            {"summary": summary, "cases": [asdict(o) for o in outcomes]}, indent=2
        )
    )
    print(f"Wrote {RESULTS / f'{stamp}.md'}")

    baseline_path = EVALS_DIR / suite.baseline_file
    if args.update_baseline:
        baseline_path.write_text(json.dumps(summary, indent=2))
        print(f"Baseline updated ({baseline_path.name}): overall {summary['overall']:.1f}")
        return 0

    if args.check:
        return compare_to_baseline(summary, baseline_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        default="bi",
        choices=sorted(SUITES),
        help="bi (saved pages) or market (query-based macro/sizing/resources)",
    )
    parser.add_argument("--case", help="comma-separated case names")
    parser.add_argument("--stages", help="comma-separated stage slugs")
    parser.add_argument("--check", action="store_true", help="fail on regression vs baseline")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--no-critic", action="store_true", help="skip the LLM review pass")
    parser.add_argument("--label", help="name for the results files")
    parser.add_argument(
        "--render", help="re-render a stored results JSON; runs no models, needs no key"
    )
    args = parser.parse_args(argv)
    if args.render:
        return render_stored(Path(args.render))
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
