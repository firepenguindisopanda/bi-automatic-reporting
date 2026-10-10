"""Repeats, the variance-aware regression rule, like-for-like subsets and
baseline merging. Offline: summaries are built from hand-made outcomes and the
stored runs in tasks/evidence/, and the CLI tests stop before any model call.
"""

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

import evals.run as run
from evals.compare import (
    BASELINE_FORMAT,
    CONFIDENCE_TOLERANCE,
    DEFAULT_K,
    EXIT_NO_BASELINE,
    EXIT_OK,
    EXIT_REGRESSION,
    NOISY_SINGLE_RUN_WARNING,
    REGRESSION_TOLERANCE,
    Comparison,
    compare_summaries,
    diff_variance,
    load_baseline,
    merge_baseline,
    report_check,
    summary_from_cells,
)
from evals.run import BI_SUITE, CaseOutcome, StageOutcome, render_markdown, summarise

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "tasks" / "evidence"
STAGES = BI_SUITE.stage_keys


def stage(name: str, completeness: float = 100.0, confidence: float | None = None, **kw) -> StageOutcome:
    return StageOutcome(stage=name, ok=True, completeness=completeness, confidence=confidence, **kw)


def runs(case: str, stage_name: str, scores: list[float], confidence: list[float] | None = None) -> list[CaseOutcome]:
    """One CaseOutcome per run of a single cell."""
    conf = confidence or [None] * len(scores)
    return [
        CaseOutcome(case=case, stages=[stage(stage_name, s, c)], run=i + 1)
        for i, (s, c) in enumerate(zip(scores, conf, strict=True))
    ]


def stored(name: str) -> list[CaseOutcome]:
    data = json.loads((EVIDENCE / name).read_text())
    return [CaseOutcome(case=c["case"], stages=[StageOutcome(**s) for s in c["stages"]]) for c in data["cases"]]


def only(outcomes: list[CaseOutcome], stages: set[str]) -> list[CaseOutcome]:
    """What a --stages run would have produced, given the same model outputs (copies)."""
    return [
        CaseOutcome(case=o.case, stages=[replace(s) for s in o.stages if s.stage in stages], run=o.run)
        for o in outcomes
    ]


def legacy(summary: dict) -> dict:
    """The same summary as a format-1 file would have held it."""
    return {k: v for k, v in summary.items() if k not in ("cells", "format", "suite")}


# --- repeats ------------------------------------------------------------------


class TestRepeats:
    def test_cells_keep_every_run_and_their_spread(self):
        summary = summarise(runs("stripe", "swot", [100.0, 75.0, 100.0], [80.0, 60.0, 70.0]))
        cell = summary["cells"]["stripe"]["swot"]
        assert cell["scores"] == [100.0, 75.0, 100.0]
        assert cell["mean"] == pytest.approx(91.67, abs=0.01)
        assert cell["stdev"] == pytest.approx(14.43, abs=0.01)
        assert cell["confidence"] == [80.0, 60.0, 70.0]
        assert cell["confidence_stdev"] == pytest.approx(10.0)

    def test_a_single_run_has_no_stdev(self):
        cell = summarise(runs("stripe", "swot", [90.0]))["cells"]["stripe"]["swot"]
        assert cell["stdev"] is None

    def test_aggregates_average_cell_means(self):
        outcomes = runs("a", "swot", [100.0, 50.0]) + runs("b", "swot", [80.0, 80.0])
        summary = summarise(outcomes)
        assert summary["per_stage"]["swot"] == 77.5
        assert summary["per_case"] == {"a": 75.0, "b": 80.0}
        assert summary["overall"] == 77.5

    def test_assertion_failures_count_in_every_run_they_happen(self):
        outcomes = [
            CaseOutcome("a", [stage("profile", 100.0, assertion_failures=["x"])], run=1),
            CaseOutcome("a", [stage("profile", 100.0)], run=2),
        ]
        assert summarise(outcomes)["cells"]["a"]["profile"]["scores"] == [85.0, 100.0]

    def test_a_single_run_summary_reproduces_the_recorded_baseline(self):
        """Format-1 keys must not move: the shipped baseline was written from this run."""
        summary = summarise(stored("rebaseline-run3-post-repair.json"))
        baseline = load_baseline(ROOT / "evals" / "baseline.json")
        for key in ("overall", "per_stage", "per_stage_confidence", "per_case", "metric_health"):
            assert summary[key] == baseline[key], key

    def test_render_shows_mean_and_spread_per_cell(self):
        outcomes = runs("stripe", "swot", [100.0, 80.0], [70.0, 90.0])
        report = render_markdown(outcomes, ["swot"])
        assert "runs per case: 2" in report
        assert "| stripe | 90±14 | 90.0 |" in report
        assert "| stripe | 80±14 | 80.0 |" in report

    def test_render_counts_failed_runs(self):
        outcomes = [
            CaseOutcome("a", [stage("swot", 100.0)], run=1),
            CaseOutcome("a", [StageOutcome(stage="swot", ok=False, error="boom")], run=2),
        ]
        report = render_markdown(outcomes, ["swot"])
        assert "(1/2 failed)" in report
        assert "**a · swot · run 2**" in report

    def test_the_repeat_flag_rejects_zero(self):
        with pytest.raises(SystemExit):
            run.main(["--repeat", "0"])


# --- the rule -----------------------------------------------------------------


class TestVariance:
    def test_no_repeats_on_either_side_means_no_estimate(self):
        assert diff_variance([90.0], [80.0]) is None
        assert diff_variance(None, [80.0]) is None

    # The sample variance of [80, 100] is 200.

    def test_a_single_run_side_borrows_the_other_sides_spread(self):
        assert diff_variance([90.0], [80.0, 100.0]) == pytest.approx(200 / 1 + 200 / 2)
        assert diff_variance([80.0, 100.0], [90.0]) == pytest.approx(200 / 2 + 200 / 1)

    def test_both_sides_use_their_own_spread(self):
        assert diff_variance([80.0, 100.0], [90.0, 90.0, 90.0]) == pytest.approx(200 / 2 + 0 / 3)

    def test_a_format_1_baseline_counts_as_one_run(self):
        assert diff_variance(None, [80.0, 100.0]) == pytest.approx(200 / 1 + 200 / 2)


class TestComparison:
    def test_without_an_estimate_the_limit_is_the_floor(self):
        c = Comparison("stage", "swot", 90.0, 84.0, floor=5.0, k=2.0)
        assert c.limit == 5.0 and c.regressed

    def test_k_standard_errors_widen_the_limit_past_the_floor(self):
        c = Comparison("stage", "swot", 90.0, 84.0, floor=5.0, k=2.0, se=4.0)
        assert c.limit == 8.0 and not c.regressed

    def test_the_floor_holds_however_steady_the_runs(self):
        assert Comparison("cell", "a/b", 90.0, 86.0, floor=5.0, k=2.0, se=0.0).regressed is False

    def test_a_drop_of_exactly_the_limit_is_not_a_regression(self):
        assert Comparison("stage", "swot", 80.0, 75.0, floor=5.0, k=2.0).regressed is False

    def test_defaults(self):
        assert (REGRESSION_TOLERANCE, DEFAULT_K, CONFIDENCE_TOLERANCE) == (5.0, 2.0, 20.0)


class TestRegressionRule:
    def test_single_run_falls_back_to_the_floor_and_warns(self):
        before = summarise(runs("a", "swot", [100.0]))
        after = summarise(runs("a", "swot", [90.0]))
        result = compare_summaries(after, before)
        assert result.single_run
        assert [c.label for c in result.regressions] == ["overall", "swot", "a/swot"]
        lines: list[str] = []
        report_check(result, floor=5.0, k=2.0, confidence_floor=20.0, out=lines.append)
        assert any(NOISY_SINGLE_RUN_WARNING in line for line in lines)

    def test_a_drop_inside_the_noise_does_not_regress(self):
        """75.0 is a value this cell really produces between unchanged runs
        (linear/swot went 25 -> 100 in tasks/evidence/run-variance.txt)."""
        before = summarise(runs("a", "swot", [100.0, 75.0, 100.0]))
        after = summarise(runs("a", "swot", [75.0, 100.0, 75.0]))
        result = compare_summaries(after, before)
        assert not result.single_run
        assert result.regressions == []

    def test_a_consistent_drop_regresses_even_with_repeats(self):
        before = summarise(runs("a", "swot", [100.0, 100.0, 100.0]))
        after = summarise(runs("a", "swot", [85.0, 85.0, 85.0]))
        assert {c.label for c in compare_summaries(after, before).regressions} == {"overall", "swot", "a/swot"}

    def test_flags_change_floor_and_k(self):
        before = summarise(runs("a", "swot", [100.0]))
        after = summarise(runs("a", "swot", [90.0]))
        assert compare_summaries(after, before, floor=12.0).regressions == []

    def test_aggregates_average_noise_away(self):
        """Seven noisy cells: each one's own limit is wide, the stage mean's is narrower."""
        before, after = [], []
        for i in range(7):
            before += runs(f"c{i}", "swot", [100.0, 75.0])
            after += runs(f"c{i}", "swot", [75.0, 75.0])
        result = compare_summaries(summarise(after), summarise(before))
        stage_limit = next(c.limit for c in result.contract if c.level == "stage")
        cell_limit = next(c.limit for c in result.contract if c.level == "cell")
        assert stage_limit < cell_limit
        assert stage_limit == pytest.approx(2 * math.sqrt((312.5 / 2 + 0 / 2) / 7))

    def test_confidence_uses_its_own_floor(self):
        before = summarise(runs("a", "swot", [100.0], [80.0]))
        after = summarise(runs("a", "swot", [100.0], [65.0]))
        result = compare_summaries(after, before)
        assert result.confidence[0].limit == CONFIDENCE_TOLERANCE
        assert result.regressions == []
        assert [c.label for c in compare_summaries(after, before, confidence_floor=10.0).regressions] == ["swot"]


# --- like-for-like subsets ---------------------------------------------------


class TestLikeForLike:
    def test_a_stage_subset_at_parity_does_not_regress(self):
        """The reported bug: --stages swot --check at exact parity said
        89.3 vs 94.7 overall, REGRESSION."""
        full = stored("rebaseline-run3-post-repair.json")
        baseline = summarise(full)
        result = compare_summaries(summarise(only(full, {"swot"})), baseline)
        overall = next(c for c in result.contract if c.level == "overall")
        assert overall.before == pytest.approx(89.29, abs=0.01)  # not 94.7
        assert overall.after == pytest.approx(overall.before)
        assert result.regressions == []

    def test_a_case_subset_compares_that_case_only(self):
        full = stored("rebaseline-run3-post-repair.json")
        current = [o for o in full if o.case == "ghost"]
        result = compare_summaries(summarise(current), summarise(full))
        overall = next(c for c in result.contract if c.level == "overall")
        assert overall.before == pytest.approx(87.5)
        assert {c.label for c in result.contract if c.level == "stage"} == set(STAGES)
        assert result.regressions == []

    def test_a_subset_regression_is_still_caught(self):
        full = stored("rebaseline-run3-post-repair.json")
        worse = only(full, {"profile"})
        worse[0].stages[0].assertion_failures = ["company_name wrong"]  # stripe profile 100 -> 85
        labels = {c.label for c in compare_summaries(summarise(worse), summarise(full)).regressions}
        assert "stripe/profile" in labels and "profile" not in labels  # one case of seven moves the mean ~2

    def test_new_and_absent_cells_are_reported_not_compared(self):
        baseline = summarise(runs("a", "swot", [90.0]))
        current = summarise(runs("a", "swot", [90.0]) + runs("b", "swot", [10.0]))
        result = compare_summaries(current, baseline)
        assert result.regressions == []
        assert any("not in the baseline" in n and "b/swot" in n for n in result.notes)

    def test_the_review_bug_is_also_fixed_against_a_format_1_baseline(self):
        full = stored("rebaseline-run3-post-repair.json")
        old_format = legacy(summarise(full))
        result = compare_summaries(summarise(only(full, {"swot"})), old_format)
        assert [c.label for c in result.contract] == ["swot"]  # overall cannot be re-cut, so it is skipped
        assert any(n.startswith("overall: not compared") for n in result.notes)
        assert result.regressions == []

    def test_a_case_subset_against_a_format_1_baseline_uses_its_case_means(self):
        full = stored("rebaseline-run3-post-repair.json")
        current = [o for o in full if o.case in {"ghost", "flyio"}]
        result = compare_summaries(summarise(current), legacy(summarise(full)))
        overall = next(c for c in result.contract if c.level == "overall")
        assert overall.before == pytest.approx((87.5 + 91.7) / 2)
        assert not [c for c in result.contract if c.level == "stage"]  # stage means cover all seven cases

    def test_a_format_1_baseline_borrows_the_runs_spread(self):
        baseline = legacy(summarise(runs("a", "swot", [100.0])))
        current = summarise(runs("a", "swot", [100.0, 75.0, 100.0]))
        result = compare_summaries(current, baseline)
        assert not result.single_run
        assert result.regressions == []

    def test_the_older_format_1_baseline_still_loads_and_compares(self):
        """baseline.pre-model-retirement.json predates confidence and cells."""
        old = load_baseline(ROOT / "evals" / "baseline.pre-model-retirement.json")
        assert "cells" not in old and "per_stage_confidence" not in old
        result = compare_summaries(summarise(stored("rebaseline-run3-post-repair.json")), old)
        assert {c.label for c in result.contract} == {"overall", *STAGES}
        assert result.confidence_notes == ["not in the baseline yet - re-baseline to start tracking"]


# --- baseline merge -------------------------------------------------------------


class TestMerge:
    def full(self) -> dict:
        return summarise(stored("rebaseline-run3-post-repair.json"))

    def test_a_subset_overwrites_only_its_cells(self):
        base = self.full()
        subset = summarise(runs("ghost", "swot", [100.0, 100.0]))
        merged = merge_baseline(base, subset, full=False)
        assert merged["cells"]["ghost"]["swot"]["scores"] == [100.0, 100.0]
        assert merged["cells"]["stripe"] == base["cells"]["stripe"]
        assert sum(len(v) for v in merged["cells"].values()) == 42
        # ghost/swot was 25.0; every aggregate is recomputed from the cells.
        assert merged["per_stage"]["swot"] == pytest.approx(round((89.3 * 7 - 25 + 100) / 7, 1), abs=0.05)
        assert merged["overall"] > base["overall"]

    def test_a_subset_never_replaces_the_whole_baseline(self):
        merged = merge_baseline(self.full(), summarise(runs("ghost", "swot", [50.0])), full=False)
        assert set(merged["per_case"]) == set(self.full()["per_case"])

    def test_a_full_run_replaces_it(self):
        replaced = merge_baseline(self.full(), summarise(runs("ghost", "swot", [50.0])), full=True)
        assert set(replaced["cells"]) == {"ghost"}
        assert replaced["overall"] == 50.0

    def test_no_existing_baseline_starts_one(self):
        created = merge_baseline(None, summarise(runs("ghost", "swot", [50.0])), full=False)
        assert created["format"] == BASELINE_FORMAT

    def test_a_format_1_baseline_cannot_take_a_subset(self):
        with pytest.raises(ValueError, match="format 1"):
            merge_baseline(legacy(self.full()), summarise(runs("ghost", "swot", [50.0])), full=False)

    def test_the_shipped_baseline_is_format_2_and_self_consistent(self):
        baseline = load_baseline(ROOT / "evals" / "baseline.json")
        assert baseline["format"] == BASELINE_FORMAT
        rebuilt = summary_from_cells(baseline["cells"])
        assert {k: v for k, v in rebuilt.items() if k != "cells"} == {
            k: v for k, v in baseline.items() if k not in ("cells", "suite")
        }


# --- exit codes and the CLI, short of any model call ------------------------


class TestExitCodes:
    def test_the_codes_are_distinct(self):
        assert len({EXIT_OK, EXIT_REGRESSION, EXIT_NO_BASELINE}) == 3
        assert EXIT_NO_BASELINE == 2

    def test_a_missing_baseline_is_not_a_regression(self, tmp_path, capsys):
        code = run.compare_to_baseline({"overall": 1.0}, tmp_path / "market-baseline.json", suite="market")
        assert code == EXIT_NO_BASELINE
        assert "no baseline for suite market (market-baseline.json); run --update-baseline" in capsys.readouterr().out

    def test_check_refuses_before_running_when_the_suite_has_no_baseline(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(run, "baseline_path", lambda suite: tmp_path / suite.baseline_file)
        monkeypatch.setattr(run.settings, "nvidia_api_key", "would-be-used-if-reached")
        monkeypatch.setattr(run, "AgentRunner", None)  # any attempt to run a stage would raise
        assert run.main(["--suite", "market", "--check"]) == EXIT_NO_BASELINE
        assert "no baseline for suite market" in capsys.readouterr().out

    def test_a_subset_update_refuses_a_format_1_baseline_before_running(self, tmp_path, monkeypatch, capsys):
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"overall": 90.0, "per_stage": {"swot": 90.0}}))
        monkeypatch.setattr(run, "baseline_path", lambda suite: path)
        monkeypatch.setattr(run.settings, "nvidia_api_key", "would-be-used-if-reached")
        monkeypatch.setattr(run, "AgentRunner", None)
        assert run.main(["--stages", "swot", "--update-baseline"]) == EXIT_NO_BASELINE
        assert "cannot be merged" in capsys.readouterr().out

    def test_an_unknown_stage_is_refused_before_running(self, monkeypatch, capsys):
        monkeypatch.setattr(run.settings, "nvidia_api_key", "would-be-used-if-reached")
        monkeypatch.setattr(run, "AgentRunner", None)
        assert run.main(["--stages", "swott"]) == 2
        assert "Unknown stage" in capsys.readouterr().out

    def test_check_exits_one_on_a_regression_against_a_format_2_file(self, tmp_path):
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(summarise(runs("a", "swot", [100.0, 100.0]))))
        worse = summarise(runs("a", "swot", [60.0, 60.0]))
        assert run.compare_to_baseline(worse, path) == EXIT_REGRESSION
        same = summarise(runs("a", "swot", [100.0, 100.0]))
        assert run.compare_to_baseline(same, path) == EXIT_OK
