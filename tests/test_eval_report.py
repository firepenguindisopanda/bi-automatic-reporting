"""Tests for the eval report renderer and its metric-health check.

No API key, no network: these drive the renderer with hand-built outcomes so
the logic runs in CI on every commit. `evals/results/` is gitignored, so the
one test that reads a stored run skips when there isn't one locally.
"""

import json
from pathlib import Path

import pytest

from evals.run import (
    BI_SUITE,
    MARKET_SUITE,
    CONFIDENCE_TOLERANCE,
    REGRESSION_TOLERANCE,
    SUITES,
    CaseOutcome,
    StageOutcome,
    _health,
    check_confidence_drift,
    load_cases,
    render_markdown,
    summarise,
)

RESULTS = Path(__file__).resolve().parents[1] / "evals" / "results"
STAGES = ["profile", "market", "competitive", "swot", "marketing", "report"]


def stage(name: str, completeness: float = 100.0, confidence: float | None = None) -> StageOutcome:
    return StageOutcome(stage=name, ok=True, completeness=completeness, confidence=confidence)


def case(name: str, **scores: float | None) -> CaseOutcome:
    return CaseOutcome(
        case=name,
        stages=[stage(k, confidence=v) for k, v in scores.items()],
    )


# --- metric health ---------------------------------------------------------


def test_health_flags_a_pinned_metric_as_dead():
    """The exact failure this module exists to surface: 100 everywhere."""
    result = _health([100.0] * 42)
    assert result["dead"] is True
    assert result["distinct"] == 1
    assert result["n"] == 42


def test_health_does_not_flag_a_metric_that_varies():
    result = _health([89.0, 70.0, 50.0, 90.0, 98.0, 83.0])
    assert result["dead"] is False
    assert result["distinct"] == 6


def test_health_needs_enough_stages_before_calling_a_metric_dead():
    """Four identical scores is a small sample, not proof the metric is broken."""
    assert _health([100.0] * 4)["dead"] is False
    assert _health([100.0] * 5)["dead"] is True


def test_health_of_nothing_is_not_dead():
    result = _health([])
    assert result["dead"] is False
    assert result["mean"] is None


# --- rendering -------------------------------------------------------------


def test_report_declares_a_dead_completeness_metric():
    outcomes = [case(f"c{i}", profile=80.0, market=90.0) for i in range(4)]
    report = render_markdown(outcomes, ["profile", "market"])
    assert "DEAD" in report
    assert "completeness" in report


def test_contract_and_confidence_stay_in_separate_tables():
    """Averaging a deterministic score with a noisy one hides which moved."""
    report = render_markdown([case("stripe", profile=70.0)], ["profile"])
    assert "## Contract score" in report
    assert "## Critic confidence" in report
    assert report.index("## Contract score") < report.index("## Critic confidence")


def test_stages_are_ranked_worst_confidence_first():
    outcomes = [case("a", profile=85.0, swot=73.0, market=77.0)]
    report = render_markdown(outcomes, ["profile", "swot", "market"])
    ranking = report.split("worst first:")[1]
    assert ranking.index("`swot`") < ranking.index("`market`") < ranking.index("`profile`")


def test_uncritiqued_stage_renders_as_absent_not_zero():
    """The report stage has critic=False; a dash is honest, a 0 is a lie."""
    outcomes = [CaseOutcome(case="a", stages=[stage("report", confidence=None)])]
    report = render_markdown(outcomes, ["report"])
    confidence_table = report.split("## Critic confidence")[1]
    assert "| a | - | - |" in confidence_table


# --- summary ---------------------------------------------------------------


def test_summary_carries_per_stage_confidence():
    summary = summarise([case("a", profile=80.0), case("b", profile=90.0)])
    assert summary["per_stage_confidence"]["profile"] == 85.0


def test_summary_reports_metric_health_for_both_metrics():
    summary = summarise([case(f"c{i}", profile=89.0) for i in range(6)])
    assert summary["metric_health"]["completeness"]["dead"] is True
    assert summary["metric_health"]["confidence"]["dead"] is True


def test_mean_confidence_is_none_when_the_critic_never_ran():
    outcome = CaseOutcome(case="a", stages=[stage("profile"), stage("market")])
    assert outcome.mean_confidence is None
    assert outcome.mean_score == 100.0


# --- regression guard against a real stored run ----------------------------

KNOWN_MEANS = {
    "swot": 73.5,
    "market": 77.0,
    "competitive": 78.3,
    "marketing": 83.4,
    "profile": 85.3,
}


@pytest.mark.skipif(
    not (RESULTS / "latest.json").exists(), reason="no local eval run to re-render"
)
def test_stored_run_reproduces_its_recorded_stage_means():
    """A renderer change must not silently move the numbers it reports."""
    data = json.loads((RESULTS / "latest.json").read_text())
    outcomes = [
        CaseOutcome(case=c["case"], stages=[StageOutcome(**s) for s in c["stages"]])
        for c in data["cases"]
    ]
    confidence = summarise(outcomes)["per_stage_confidence"]
    for stage_name, expected in KNOWN_MEANS.items():
        assert confidence[stage_name] == pytest.approx(expected, abs=0.05), stage_name


# --- confidence gating (M2 earns this; SPEC open question 1) ---------------


def _summary(**confidence: float) -> dict:
    return {"per_stage_confidence": dict(confidence)}


def _baseline(**confidence: float) -> dict:
    return {"per_stage_confidence": dict(confidence)}


def test_confidence_drop_beyond_tolerance_regresses():
    regressed = check_confidence_drift(
        _summary(swot=60.0), _baseline(swot=60.0 + CONFIDENCE_TOLERANCE + 0.1)
    )
    assert regressed == ["swot"]


def test_a_drop_inside_tolerance_does_not_regress():
    """The critic is noisy; the tolerance exists to absorb that, not to be nudged."""
    regressed = check_confidence_drift(
        _summary(swot=60.0), _baseline(swot=60.0 + CONFIDENCE_TOLERANCE)
    )
    assert regressed == []


class TestConfidenceHasItsOwnTolerance:
    """Measured 2026-08-26: two full runs, no code change between them.

    Per-stage confidence moved 6.6 to 13.7 points on its own and 5 of 5 stages
    breached 5.0, so holding confidence to the completeness tolerance made the
    gate fire on every run. Evidence in tasks/evidence/run-variance.txt.
    """

    def test_the_two_tolerances_are_not_the_same_number(self):
        assert CONFIDENCE_TOLERANCE != REGRESSION_TOLERANCE

    def test_confidence_tolerance_clears_the_observed_noise(self):
        """13.7 was the largest per-stage move between two unchanged runs."""
        assert CONFIDENCE_TOLERANCE > 13.7

    def test_a_move_the_size_of_observed_noise_does_not_regress(self):
        """The exact worst case seen: marketing 75.0 -> 61.3 with no change."""
        assert check_confidence_drift(_summary(marketing=61.3), _baseline(marketing=75.0)) == []

    def test_a_real_collapse_still_regresses(self):
        """Widening the band must not cost the gate its purpose: a stage that
        loses its grounding drops far further than the noise floor."""
        assert check_confidence_drift(_summary(swot=20.0), _baseline(swot=75.0)) == ["swot"]

    def test_completeness_keeps_the_tighter_band(self):
        """Completeness moved 0.0 to 10.7 across the same two runs, and is
        deterministic given the same output - it does not need the wider band."""
        assert REGRESSION_TOLERANCE == 5.0


def test_an_improvement_does_not_regress():
    assert check_confidence_drift(_summary(swot=90.0), _baseline(swot=70.0)) == []


def test_a_stage_absent_from_the_baseline_is_new_not_worse():
    regressed = check_confidence_drift(
        _summary(swot=10.0, market=80.0), _baseline(market=80.0)
    )
    assert regressed == []


def test_only_the_regressed_stages_are_reported():
    regressed = check_confidence_drift(
        _summary(swot=40.0, market=80.0, profile=20.0),
        _baseline(swot=90.0, market=80.0, profile=90.0),
    )
    assert sorted(regressed) == ["profile", "swot"]


def test_a_baseline_without_confidence_cannot_fail_the_build():
    """It has to be re-baselined once, deliberately, before it can gate."""
    assert check_confidence_drift(_summary(swot=10.0), {"per_stage": {"swot": 100.0}}) == []


def test_a_run_without_confidence_cannot_fail_the_build():
    assert check_confidence_drift({}, _baseline(swot=90.0)) == []


def test_a_stage_the_critic_never_scored_is_absent_rather_than_zero():
    """`report` carries critic=False, so it has no score at all.

    Treating that as 0.0 would make it the worst stage in every run and would
    fail --check permanently.
    """
    outcome = CaseOutcome(
        case="stripe",
        stages=[stage("profile", confidence=85.0), stage("report", confidence=None)],
    )
    per_stage = summarise([outcome])["per_stage_confidence"]
    assert "report" not in per_stage
    assert per_stage["profile"] == 85.0
    assert check_confidence_drift({"per_stage_confidence": per_stage}, _baseline(profile=85.0)) == []


# --- the gate itself, not just its helper ---------------------------------


def _write_baseline(tmp_path, monkeypatch, **confidence):
    """A baseline whose completeness is fine, so only confidence can fail it."""
    import evals.run as run

    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "overall": 90.0,
                "per_stage": {"swot": 90.0},
                "per_stage_confidence": dict(confidence),
            }
        )
    )
    monkeypatch.setattr(run, "BASELINE", path)
    return run


def test_check_exits_nonzero_on_a_confidence_regression(tmp_path, monkeypatch):
    """The whole point of T2: the helper's verdict has to reach the exit code."""
    run = _write_baseline(tmp_path, monkeypatch, swot=90.0)
    summary = {
        "overall": 90.0,
        "per_stage": {"swot": 90.0},
        "per_stage_confidence": {"swot": 40.0},
    }
    assert run.compare_to_baseline(summary) == 1


def test_check_exits_zero_when_only_confidence_improves(tmp_path, monkeypatch):
    run = _write_baseline(tmp_path, monkeypatch, swot=70.0)
    summary = {
        "overall": 90.0,
        "per_stage": {"swot": 90.0},
        "per_stage_confidence": {"swot": 95.0},
    }
    assert run.compare_to_baseline(summary) == 0


def test_check_still_exits_zero_against_the_current_confidence_free_baseline(
    tmp_path, monkeypatch
):
    """`evals/baseline.json` has no confidence yet, so nothing can gate on it."""
    import evals.run as run

    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"overall": 90.0, "per_stage": {"swot": 90.0}}))
    monkeypatch.setattr(run, "BASELINE", path)
    summary = {
        "overall": 90.0,
        "per_stage": {"swot": 90.0},
        "per_stage_confidence": {"swot": 10.0},
    }
    assert run.compare_to_baseline(summary) == 0


# --- M5 / T6a: the market suite runs at all --------------------------------
#
# `evals/run.py` iterated BI_SPECS only, so macro, sizing and resources had
# never been executed by the harness. That is the largest coverage gap in the
# repo and the direct reason the CircleCI error shipped unnoticed. These tests
# are offline: they check the wiring, not the models.


class TestSuites:
    def test_both_suites_are_registered(self):
        assert set(SUITES) == {"bi", "market"}

    def test_the_market_suite_runs_macro_then_sizing_then_resources(self):
        assert MARKET_SUITE.stage_keys == ["macro", "sizing", "resources"]

    def test_the_bi_suite_is_unchanged(self):
        assert BI_SUITE.stage_keys == [
            "profile", "market", "competitive", "swot", "marketing", "report",
        ]

    def test_the_suites_do_not_share_a_baseline(self):
        """A market run must not be compared against, or overwrite, the BI baseline."""
        assert BI_SUITE.baseline_file != MARKET_SUITE.baseline_file

    def test_the_suites_do_not_share_a_case_file(self):
        assert BI_SUITE.cases_file != MARKET_SUITE.cases_file

    def test_a_market_context_carries_the_query_and_no_scrape(self):
        """These stages take a query, not a saved page - there is nothing to scrape."""
        ctx = MARKET_SUITE.build_context({"market_query": "Self-hosted CI/CD tools"})
        assert ctx.market_query == "Self-hosted CI/CD tools"
        assert ctx.scraped is None

    def test_resources_is_terminal_and_feeds_nothing(self):
        """It has no StageContext field; the runner must not try to set one."""
        assert "resources" not in MARKET_SUITE.ctx_field
        assert set(MARKET_SUITE.ctx_field) == {"macro", "sizing"}

    def test_every_mapped_context_field_exists_on_StageContext(self):
        """A typo here would only surface mid-run, after paying for a stage."""
        from app.analysis.runner import StageContext

        for suite in SUITES.values():
            for stage, attr in suite.ctx_field.items():
                assert hasattr(StageContext(), attr), f"{suite.name}/{stage} -> {attr}"


class TestMarketCases:
    def test_the_seeded_case_is_loadable(self):
        cases = load_cases(MARKET_SUITE)
        assert [c["name"] for c in cases] == ["self-hosted-cicd"]

    def test_the_seeded_case_carries_the_disqualifier_that_started_this(self):
        case = load_cases(MARKET_SUITE)[0]
        assert "self-hosted" in case["market_query"].lower()
        assert "CircleCI" in case["must_exclude"]
        assert "Travis CI" in case["must_exclude"]

    def test_every_market_case_has_a_query(self):
        for case in load_cases(MARKET_SUITE):
            assert case.get("market_query"), case["name"]

    def test_the_bi_cases_still_load(self):
        assert len(load_cases(BI_SUITE)) == 7


class TestOneRendererServesBothSuites:
    def test_market_stages_render_through_the_same_renderer(self):
        outcome = CaseOutcome(
            case="self-hosted-cicd",
            stages=[
                stage("macro", completeness=100.0, confidence=70.0),
                stage("sizing", completeness=87.5, confidence=64.0),
                stage("resources", completeness=54.5, confidence=61.0),
            ],
        )
        report = render_markdown([outcome], MARKET_SUITE.stage_keys)
        assert "macro" in report and "sizing" in report and "resources" in report
        assert "## Metric health" in report
        assert "## Critic confidence" in report

    def test_metric_health_sees_variation_the_bi_suite_could_not(self):
        """Three distinct completeness values, so the metric is not DEAD."""
        outcome = CaseOutcome(
            case="self-hosted-cicd",
            stages=[
                stage("macro", completeness=100.0),
                stage("sizing", completeness=87.5),
                stage("resources", completeness=54.5),
            ],
        )
        health = summarise([outcome])["metric_health"]["completeness"]
        assert health["distinct"] == 3
        assert health["dead"] is False
