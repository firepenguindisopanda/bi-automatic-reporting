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
    uv run python -m evals.run --repeat 3         # every case 3 times: mean and spread
    uv run python -m evals.run --check            # fail on a completeness or
                                                  # confidence regression
    uv run python -m evals.run --update-baseline  # a --case/--stages run merges

Exit codes: 0 ok, 1 regression, 2 nothing could be compared or run (no
baseline for the suite, no API key, a bad argument or case file).

Needs NVIDIA_API_KEY: the agents are real. Only the network fetch is stubbed.
The regression rule and baseline format are documented in evals/compare.py.
"""

import argparse
import asyncio
import json
import math
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
from app.scraper.engine import ScraperEngine
from evals.assertions import iter_strings, parse_assertions, plain
from evals.compare import (
    CONFIDENCE_TOLERANCE,
    DEFAULT_K,
    EXIT_NO_BASELINE,
    EXIT_OK,
    EXIT_REGRESSION,
    REGRESSION_TOLERANCE,
    Cells,
    compare_summaries,
    describe,
    health,
    load_baseline,
    merge_baseline,
    missing_baseline_message,
    report_check,
    summary_from_cells,
)

EVALS_DIR = Path(__file__).parent
FIXTURES = EVALS_DIR / "fixtures"
RESULTS = EVALS_DIR / "results"
BASELINE = EVALS_DIR / "baseline.json"


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
    run: int = 1
    """1-based repeat number, for --repeat."""

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


def baseline_path(suite: Suite) -> Path:
    return EVALS_DIR / suite.baseline_file


def load_cases(suite: Suite = BI_SUITE) -> list[dict[str, Any]]:
    """The suite's cases, with every stage assertion validated up front.

    A typo in a stage name or a regex fails here, for free, rather than after
    the run has paid for the stage it was meant to check.
    """
    raw = yaml.safe_load((EVALS_DIR / suite.cases_file).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = list(raw["cases"])
    for case in cases:
        parse_assertions(case, suite.stage_keys)
    return cases


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
    return " ".join(iter_strings(plain(value))).lower()


def check_assertions(stage: str, value: Any, case: dict[str, Any]) -> list[str]:
    """Per-case expectations.

    ``forbidden`` applies to every stage, and ``assertions`` (see
    evals/assertions.py) to whichever stages each entry names. The remaining
    fields check grounded facts that only the profile stage carries.
    """
    failures: list[str] = []
    blob = _value_text(value)

    for forbidden in case.get("forbidden", []):
        if forbidden.lower() in blob:
            failures.append(f"contains forbidden text {forbidden!r}")

    for assertion in parse_assertions(case):
        if assertion.applies_to(stage):
            failures.extend(assertion.evaluate(stage, value))

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


# --- Summaries ----------------------------------------------------------------


def build_cells(outcomes: list[CaseOutcome]) -> Cells:
    """Every run's score per (case, stage). See evals/compare.py for the shape."""
    cells: Cells = {}
    for outcome in outcomes:
        for s in outcome.stages:
            cell = cells.setdefault(outcome.case, {}).setdefault(
                s.stage, {"scores": [], "completeness": [], "confidence": []}
            )
            cell["scores"].append(s.score)
            if s.completeness is not None:
                cell["completeness"].append(s.completeness)
            if s.confidence is not None:
                cell["confidence"].append(s.confidence)
    for stages in cells.values():
        for cell in stages.values():
            cell["mean"], cell["stdev"] = describe(cell["scores"])
            cell["confidence_mean"], cell["confidence_stdev"] = describe(cell["confidence"])
    return cells


def summarise(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    return summary_from_cells(build_cells(outcomes))


def _collect(outcomes: list[CaseOutcome], attr: str) -> dict[str, list[float]]:
    """Per-stage values of one metric, skipping stages that never recorded it."""
    out: dict[str, list[float]] = {}
    for outcome in outcomes:
        for s in outcome.stages:
            value = getattr(s, attr)
            if value is not None:
                out.setdefault(s.stage, []).append(value)
    return out


# --- Rendering ----------------------------------------------------------------


def _health_line(label: str, values: list[float]) -> str:
    """One line saying whether a metric can still tell two runs apart."""
    if not values:
        return f"- **{label}**: not recorded"
    result = health(values)
    plural = "" if result["distinct"] == 1 else "s"
    note = ""
    if result["dead"]:
        note = " - **DEAD**: one value everywhere, it cannot rank runs"
    elif result["n"] >= 10 and result["distinct"] <= 2:
        note = " - **near-dead**: two values across many stages"
    return (
        f"- **{label}**: {result['n']} stages, {result['distinct']} distinct value{plural}, "
        f"mean {result['mean']}{note}"
    )


def _row(case: str, cells: list[str], mean_cell: str) -> str:
    return f"| {case} | " + " | ".join(cells) + f" | {mean_cell} |"


def _spread(values: list[float]) -> str:
    """One value as itself; several as mean ± sample standard deviation."""
    if len(values) == 1:
        return f"{values[0]:.0f}"
    return f"{statistics.mean(values):.0f}±{statistics.stdev(values):.0f}"


def render_markdown(outcomes: list[CaseOutcome], stages: list[str]) -> str:
    """One renderer for both suites - `stages` is already resolved by the caller.

    With --repeat a case has several outcomes, and a cell shows the mean and
    spread of its runs. A single run renders exactly as it always has.
    """
    columns = stages or BI_SUITE.stage_keys
    header = ["| Case | " + " | ".join(columns) + " | mean |", "|" + "---|" * (len(columns) + 2)]
    by_case: dict[str, list[CaseOutcome]] = {}
    for outcome in outcomes:
        by_case.setdefault(outcome.case, []).append(outcome)
    repeats = max((len(runs) for runs in by_case.values()), default=1)
    summary = summarise(outcomes)

    def runs_of(runs: list[CaseOutcome], stage: str) -> list[StageOutcome]:
        return [s for run in runs for s in run.stages if s.stage == stage]

    def contract_cell(cell: list[StageOutcome]) -> str:
        if not cell:
            return "-"
        if not any(s.ok for s in cell):
            return "**fail**"
        text = _spread([s.score for s in cell]) + ("!" if any(s.assertion_failures for s in cell) else "")
        failed = sum(not s.ok for s in cell)
        return text + (f" ({failed}/{len(cell)} failed)" if failed else "")

    def confidence_cell(cell: list[StageOutcome]) -> str:
        values = [s.confidence for s in cell if s.ok and s.confidence is not None]
        return _spread(values) if values else "-"

    runs_note = f" · runs per case: {repeats}" if repeats > 1 else ""
    spread_note = [f"Cells show mean±stdev over up to {repeats} runs.", ""] if repeats > 1 else []
    lines = [
        "# Eval results",
        "",
        f"Cases: {len(by_case)} · stages: {', '.join(columns)}{runs_note}",
        "",
        "## Contract score",
        "",
        "Deterministic: completeness, less 15 per failed case assertion.",
        "",
        *spread_note,
        *header,
    ]
    for case_name, runs in by_case.items():
        cells = [contract_cell(runs_of(runs, stage)) for stage in columns]
        lines.append(_row(case_name, cells, f"{summary['per_case'].get(case_name, 0.0):.1f}"))

    lines += ["", f"**Overall: {summary['overall']:.1f}**", "", "`!` marks a failed case assertion.", ""]

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
    for case_name, runs in by_case.items():
        cells = [confidence_cell(runs_of(runs, stage)) for stage in columns]
        conf = [s.confidence for run in runs for s in run.stages if s.confidence is not None]
        lines.append(_row(case_name, cells, f"{statistics.mean(conf):.1f}" if conf else "-"))
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
        (o, s)
        for o in outcomes
        for s in o.stages
        if s.assertion_failures or not s.ok or s.warnings
    ]
    if problems:
        lines += ["## Findings", ""]
        for o, s in problems:
            run_label = f" · run {o.run}" if repeats > 1 else ""
            lines.append(f"**{o.case} · {s.stage}{run_label}**")
            if s.error:
                lines.append(f"- failed: {s.error}")
            for failure in s.assertion_failures:
                lines.append(f"- assertion: {failure}")
            for warning in s.warnings[:4]:
                lines.append(f"- contract: {warning}")
            lines.append("")
    return "\n".join(lines)


# --- Baseline checks --------------------------------------------------------------


def compare_to_baseline(
    summary: dict[str, Any],
    path: Path | None = None,
    *,
    floor: float = REGRESSION_TOLERANCE,
    k: float = DEFAULT_K,
    confidence_floor: float = CONFIDENCE_TOLERANCE,
    suite: str = "bi",
    out: Callable[[str], None] = print,
) -> int:
    """Compare against a suite's baseline. Defaults to the BI one.

    Returns EXIT_OK, EXIT_REGRESSION, or EXIT_NO_BASELINE when there is no
    baseline to compare against.
    """
    path = path or BASELINE
    if not path.exists():
        out(missing_baseline_message(suite, path))
        return EXIT_NO_BASELINE

    result = compare_summaries(summary, load_baseline(path), floor=floor, k=k, confidence_floor=confidence_floor)
    out("")
    out(f"Against baseline ({path.name}):")
    report_check(result, floor=floor, k=k, confidence_floor=confidence_floor, out=out)
    return EXIT_REGRESSION if result.regressions else EXIT_OK


def check_confidence_drift(
    summary: dict[str, Any],
    baseline: dict[str, Any],
    *,
    floor: float = CONFIDENCE_TOLERANCE,
    k: float = DEFAULT_K,
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

    A stage the critic never scored never reaches here: the cells only record
    confidence that exists. Absent is not the same as zero - ``report`` carries
    ``critic=False`` and would otherwise look like the worst stage in the run.
    """
    if not summary.get("per_stage_confidence") and not summary.get("cells"):
        return []
    result = compare_summaries(summary, baseline, k=k, confidence_floor=floor)
    if result.confidence or result.confidence_notes:
        print("\n  confidence:")
        for note in result.confidence_notes:
            print(f"    {note}")
        for c in result.confidence:
            print(f"    {c.line()}")
    return [c.label for c in result.confidence if c.regressed]


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
            case=case["case"],
            stages=[StageOutcome(**stage) for stage in case["stages"]],
            run=case.get("run", 1),
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


# --- Entry point ----------------------------------------------------------------


def _preflight(args: argparse.Namespace, suite: Suite, *, full_run: bool) -> int | None:
    """Refuse, before any model is paid for, a run whose ending is already known."""
    path = baseline_path(suite)
    if args.check and not args.update_baseline and not path.exists():
        print(missing_baseline_message(suite.name, path))
        return EXIT_NO_BASELINE
    if args.update_baseline and not full_run and path.exists() and not load_baseline(path).get("cells"):
        print(
            f"{path.name} has no per-cell scores (format 1), so a --case/--stages run cannot be merged "
            "into it. Record a full run with --update-baseline once."
        )
        return EXIT_NO_BASELINE
    return None


async def main_async(args: argparse.Namespace) -> int:
    suite = SUITES[args.suite]
    try:
        all_cases = load_cases(suite)
    except ValueError as e:
        print(f"Invalid {suite.cases_file}: {e}")
        return 2
    cases = all_cases
    if args.case:
        wanted = set(args.case.split(","))
        cases = [c for c in all_cases if c["name"] in wanted]
        if not cases:
            print(f"No case matched {args.case!r}")
            return 2

    stages = [s.strip() for s in args.stages.split(",")] if args.stages else []
    unknown = [s for s in stages if s not in suite.stage_keys]
    if unknown:
        print(f"Unknown stage(s) {unknown}; the {suite.name} suite has {suite.stage_keys}")
        return 2
    full_run = len(cases) == len(all_cases) and (not stages or set(stages) == set(suite.stage_keys))

    refused = _preflight(args, suite, full_run=full_run)
    if refused is not None:
        return refused

    if not settings.nvidia_api_key:
        print("NVIDIA_API_KEY is not set - the agents are real, so this cannot run.")
        return 2

    if args.no_critic:
        settings.enable_stage_critic = False
    runner = AgentRunner(
        critic=build_critic(), critic_retry_threshold=settings.critic_retry_threshold
    )

    print(
        f"Running {len(cases)} {suite.name} case(s) x {args.repeat} run(s), "
        f"critic={'on' if settings.enable_stage_critic else 'off'}\n"
    )
    outcomes: list[CaseOutcome] = []
    for run in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"Run {run}/{args.repeat}\n")
        for case in cases:
            print(f"  {case['name']}  ({case.get(suite.subject_key, '')})")
            outcome = await run_case(case, runner, stages, suite)
            outcome.run = run
            outcomes.append(outcome)
            print()

    summary = summarise(outcomes)
    summary["suite"] = suite.name
    columns = stages or suite.stage_keys
    report = render_markdown(outcomes, columns)
    print(report)

    RESULTS.mkdir(parents=True, exist_ok=True)
    # Suite-scoped so a market run cannot clobber `latest.json`, which is the
    # BI run the M1 regression test reads.
    default_stamp = "latest" if suite is BI_SUITE else f"{suite.name}-latest"
    stamp = args.label or default_stamp
    report_path = RESULTS / f"{stamp}.md"
    report_path.write_text(report, encoding="utf-8")
    (RESULTS / f"{stamp}.json").write_text(
        json.dumps(
            {"repeats": args.repeat, "summary": summary, "cases": [asdict(o) for o in outcomes]},
            indent=2,
        )
    )
    print(f"Wrote {report_path}")

    path = baseline_path(suite)
    if args.update_baseline:
        existing = load_baseline(path) if path.exists() else None
        try:
            updated = merge_baseline(existing, summary, full=full_run)
        except ValueError as e:
            print(f"Baseline not updated: {e}")
            return EXIT_NO_BASELINE
        updated["suite"] = suite.name
        path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        if existing is None:
            how = "created" + ("" if full_run else " from a partial run")
        else:
            how = "replaced" if full_run else "updated: this run's cells merged in"
        print(f"Baseline {how} ({path.name}): overall {updated['overall']:.1f}")
        return EXIT_OK

    if args.check:
        lines: list[str] = []

        def out(line: str) -> None:
            print(line)
            lines.append(line)

        code = compare_to_baseline(
            summary, path, floor=args.floor, k=args.k, confidence_floor=args.confidence_floor,
            suite=suite.name, out=out,
        )
        # Into the report too, so the CI run summary says why the gate passed or failed.
        verdict = "\n".join(lines).strip()
        report_path.write_text(f"{report}\n## Against baseline\n\n```\n{verdict}\n```\n", encoding="utf-8")
        return code
    return EXIT_OK


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _non_negative(text: str) -> float:
    value = float(text)
    if not value >= 0 or math.isinf(value):
        raise argparse.ArgumentTypeError("must be a finite number, 0 or more")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--suite",
        default="bi",
        choices=sorted(SUITES),
        help="bi (saved pages) or market (query-based macro/sizing/resources)",
    )
    parser.add_argument("--case", help="comma-separated case names")
    parser.add_argument("--stages", help="comma-separated stage slugs")
    parser.add_argument(
        "--repeat", type=_positive_int, default=1, metavar="N",
        help="run every case N times; keeps each run's score plus the per-cell mean and stdev (default 1)",
    )
    parser.add_argument("--check", action="store_true", help="fail on regression vs baseline")
    parser.add_argument(
        "--floor", type=_non_negative, default=REGRESSION_TOLERANCE,
        help=f"smallest contract-score drop that can regress (default {REGRESSION_TOLERANCE:g})",
    )
    parser.add_argument(
        "--k", type=_non_negative, default=DEFAULT_K,
        help=f"standard errors a drop must exceed to regress (default {DEFAULT_K:g})",
    )
    parser.add_argument(
        "--confidence-floor", type=_non_negative, default=CONFIDENCE_TOLERANCE,
        help=f"smallest per-stage confidence drop that can regress (default {CONFIDENCE_TOLERANCE:g})",
    )
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="record this run as the baseline; a --case/--stages run merges its cells into it",
    )
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
