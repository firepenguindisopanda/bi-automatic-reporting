"""Summaries, baselines and the regression rule. Pure: no app imports, no models.

A summary keeps the aggregate keys baseline format 1 had (``overall``,
``per_stage``, ``per_stage_confidence``, ``per_case``, ``metric_health``) and
adds ``cells``, one entry per (case, stage) with every run's value:

    cells[case][stage] = {"scores": [...], "completeness": [...], "confidence": [...],
                          "mean": .., "stdev": .., "confidence_mean": .., "confidence_stdev": ..}

Every aggregate is derived from the cells, which is what lets a subset of them
be re-aggregated for a like-for-like check or merged into a baseline. The stored
mean/stdev are for people; comparisons always recompute from the lists.

The regression rule
-------------------
A unit - one (case, stage) cell, one stage mean, or the overall mean -
regresses only when its mean drops by more than

    max(floor, k * SE)      SE^2 = s_base^2 / n_base + s_now^2 / n_now

where s is a cell's sample standard deviation across repeated runs (--repeat)
and n its number of runs. A side with a single run borrows the other side's s.
An aggregate's SE comes from its cells' SEs through its averaging weights. With
no repeats on either side there is no s at all: the limit is the floor, and the
check warns that one run against one run is mostly noise.

Measured 2026-08-26 by running the full BI suite twice with no code change
between the runs (both saved under tasks/evidence/):

  completeness  per-stage mean moved  0.0 to 10.7   2 of 6 stages over 5.0
                single cells moved up to 75.0
  confidence    per-stage mean moved  6.6 to 13.7   5 of 5 stages over 5.0
"""

import copy
import json
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_NO_BASELINE = 2
"""Distinct from a regression, so CI can tell "worse" from "nothing to compare"."""

BASELINE_FORMAT = 2
"""Format 1 (no ``format`` key) holds aggregates only. Format 2 adds ``cells``.
Both are read; only format 2 is written."""

REGRESSION_TOLERANCE = 5.0
"""Default floor for the contract score (``--floor``): the most any unit may
drop before anyone looks, however steady its repeats were. Completeness is
deterministic given an output, so it can be this tight."""

# Confidence needs its own, wider floor. At cell level it is noisier still:
# mean absolute move 15.0, max 63.0, with 65% of cells over 5.0 between unchanged
# runs, so a 5.0 floor fires on every run regardless of the change under test -
# the noisy cousin of the dead metric this harness exists to expose. 20.0 sits
# above the largest per-stage move observed (13.7) and still catches a stage
# losing its grounding, which drops much further. With --repeat the k*SE term
# carries the noise, so --confidence-floor may go lower; widen the default only
# with a fresh measurement attached.
CONFIDENCE_TOLERANCE = 20.0
"""Default floor for per-stage critic confidence (``--confidence-floor``)."""

DEFAULT_K = 2.0
"""Default number of standard errors a drop must exceed (``--k``)."""

NOISY_SINGLE_RUN_WARNING = (
    "WARNING: neither the baseline nor this run has repeats, so there is no variance "
    "estimate and every limit is the fixed floor. Single-run comparisons are noisy: with "
    "no code change a stage mean has moved 10.7 points and a single cell 75 "
    "(tasks/evidence/run-variance.txt). Re-run with --repeat N before trusting a REGRESSION."
)

Cells = dict[str, dict[str, dict[str, Any]]]
Key = tuple[str, str]


# --- Summaries ----------------------------------------------------------------


def health(values: list[float]) -> dict[str, Any]:
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


def describe(values: list[float]) -> tuple[float | None, float | None]:
    """Rounded mean and sample standard deviation; stdev needs two runs."""
    mean = round(statistics.mean(values), 2) if values else None
    stdev = round(statistics.stdev(values), 2) if len(values) >= 2 else None
    return mean, stdev


def values_of(cells: Cells, key: Key, metric: str) -> list[float]:
    cell = cells.get(key[0], {}).get(key[1])
    return [float(v) for v in (cell or {}).get(metric) or []]


def keys_of(cells: Cells, metric: str = "scores") -> list[Key]:
    """Every (case, stage) holding at least one value of ``metric``, in run order."""
    return [
        (case, stage) for case, stages in cells.items() for stage in stages if values_of(cells, (case, stage), metric)
    ]


def summary_from_cells(cells: Cells) -> dict[str, Any]:
    """Every aggregate, recomputed from the cells.

    For a single run this reproduces format 1 exactly, rounding included: a
    case mean is rounded before the overall averages it, as it always was.
    """
    per_stage: dict[str, list[float]] = {}
    per_stage_confidence: dict[str, list[float]] = {}
    per_case: dict[str, float] = {}
    completeness: list[float] = []
    confidence: list[float] = []
    for case, stages in cells.items():
        case_means = []
        for stage in stages:
            scores = values_of(cells, (case, stage), "scores")
            if scores:
                case_means.append(statistics.mean(scores))
                per_stage.setdefault(stage, []).append(statistics.mean(scores))
            conf = values_of(cells, (case, stage), "confidence")
            if conf:
                per_stage_confidence.setdefault(stage, []).append(statistics.mean(conf))
            completeness += values_of(cells, (case, stage), "completeness")
            confidence += conf
        if case_means:
            per_case[case] = round(statistics.mean(case_means), 1)

    return {
        "format": BASELINE_FORMAT,
        "overall": round(statistics.mean(per_case.values()), 1) if per_case else 0.0,
        "per_stage": {k: round(statistics.mean(v), 1) for k, v in per_stage.items()},
        "per_stage_confidence": {k: round(statistics.mean(v), 1) for k, v in per_stage_confidence.items()},
        "per_case": per_case,
        "metric_health": {"completeness": health(completeness), "confidence": health(confidence)},
        "cells": cells,
    }


# --- Baselines ------------------------------------------------------------------


def load_baseline(path: Path) -> dict[str, Any]:
    """Either format. A format-1 file simply has no ``cells``."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def merge_baseline(existing: dict[str, Any] | None, summary: dict[str, Any], *, full: bool) -> dict[str, Any]:
    """The baseline after recording ``summary``.

    A full run replaces the baseline outright. A subset run (--case/--stages)
    overwrites only the cells it ran, keeps every other cell, and re-aggregates,
    so a partial run can no longer pass itself off as the whole baseline.
    """
    if full or existing is None:
        return summary_from_cells(copy.deepcopy(summary["cells"]))
    if not existing.get("cells"):
        raise ValueError(
            "the existing baseline has no per-cell scores (format 1), so a subset run cannot be "
            "merged into it; record a full run with --update-baseline once"
        )
    cells: Cells = copy.deepcopy(existing["cells"])
    for case, stages in summary["cells"].items():
        for stage, cell in stages.items():
            cells.setdefault(case, {})[stage] = copy.deepcopy(cell)
    return summary_from_cells(cells)


def missing_baseline_message(suite: str, path: Path) -> str:
    return (
        f"no baseline for suite {suite} ({path.name}); run --update-baseline. "
        f"Nothing was compared, so this exits {EXIT_NO_BASELINE}, not {EXIT_REGRESSION}."
    )


# --- Variance -------------------------------------------------------------------


def diff_variance(before: Sequence[float] | None, after: Sequence[float], before_n: int = 1) -> float | None:
    """Variance of mean(after) - mean(before): s_b^2/n_b + s_a^2/n_a.

    A side with fewer than two runs has no standard deviation of its own and
    borrows the other side's. ``before=None`` is a baseline with no per-run
    values at all (format 1), taken as ``before_n`` runs. None when neither side
    has repeats - there is then nothing to estimate the noise from.
    """
    n_b = len(before) if before is not None else before_n
    n_a = len(after)
    if n_a == 0 or n_b == 0:
        return None
    own_b = statistics.variance(before) if before is not None and len(before) >= 2 else None
    own_a = statistics.variance(after) if len(after) >= 2 else None
    s2_b = own_b if own_b is not None else own_a
    s2_a = own_a if own_a is not None else own_b
    if s2_b is None or s2_a is None:
        return None
    return s2_b / n_b + s2_a / n_a


def combined_se(weights: dict[Key, float], variance: Callable[[Key], float | None]) -> float | None:
    """SE of a weighted mean of cell differences. Cells with no estimate add nothing."""
    known = [w * w * v for key, w in weights.items() if (v := variance(key)) is not None]
    return math.sqrt(sum(known)) if known else None


def stage_weights(keys: Sequence[Key], stage: str) -> dict[Key, float]:
    """A stage mean is the plain mean of its cells."""
    picked = [k for k in keys if k[1] == stage]
    return {k: 1 / len(picked) for k in picked}


def overall_weights(keys: Sequence[Key]) -> dict[Key, float]:
    """Overall is the mean over cases of each case's mean over its stages."""
    by_case: dict[str, list[Key]] = {}
    for k in keys:
        by_case.setdefault(k[0], []).append(k)
    return {k: 1 / (len(by_case) * len(ks)) for ks in by_case.values() for k in ks}


def _weighted(weights: dict[Key, float], cells: Cells, metric: str) -> float:
    return sum(w * statistics.mean(values_of(cells, key, metric)) for key, w in weights.items())


def _ordered_stages(keys: Sequence[Key]) -> list[str]:
    return list(dict.fromkeys(k[1] for k in keys))


def _key_list(keys: Sequence[Key], limit: int = 6) -> str:
    shown = ", ".join(f"{c}/{s}" for c, s in keys[:limit])
    return shown + (f", +{len(keys) - limit} more" if len(keys) > limit else "")


# --- Comparison -----------------------------------------------------------------


@dataclass(frozen=True)
class Comparison:
    """One unit's verdict under the regression rule."""

    level: str  # "overall", "stage" or "cell"
    label: str
    before: float
    after: float
    floor: float
    k: float
    se: float | None = None
    """Standard error of (after - before); None when no repeats exist to estimate it."""

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def limit(self) -> float:
        return self.floor if self.se is None else max(self.floor, self.k * self.se)

    @property
    def regressed(self) -> bool:
        # The epsilon keeps a drop of exactly the limit on the safe side of float noise.
        return self.delta < -self.limit - 1e-9

    def line(self) -> str:
        if self.se is None:
            basis = "floor, no variance estimate"
        elif self.k * self.se <= self.floor:
            basis = f"floor; SE {self.se:.1f}"
        else:
            basis = f"{self.k:g} x SE {self.se:.1f}"
        verdict = "REGRESSION" if self.regressed else "ok"
        return (
            f"{self.label:16} {self.before:5.1f} -> {self.after:5.1f} ({self.delta:+5.1f})"
            f"  limit {self.limit:4.1f} ({basis})  {verdict}"
        )


@dataclass
class CheckResult:
    contract: list[Comparison] = field(default_factory=list)
    confidence: list[Comparison] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    confidence_notes: list[str] = field(default_factory=list)

    @property
    def regressions(self) -> list[Comparison]:
        return [c for c in (*self.contract, *self.confidence) if c.regressed]

    @property
    def single_run(self) -> bool:
        """True when something was compared and nothing had a variance estimate."""
        compared = [*self.contract, *self.confidence]
        return bool(compared) and all(c.se is None for c in compared)


def _compare_cells(
    result: CheckResult, now: Cells, base: Cells, floor: float, k: float, confidence_floor: float
) -> None:
    """Like-for-like: the baseline is re-aggregated over exactly the cells this run has."""
    keys = [key for key in keys_of(now) if values_of(base, key, "scores")]
    shared = set(keys)
    new = [key for key in keys_of(now) if key not in shared]
    absent = [key for key in keys_of(base) if key not in shared]
    cases = list(dict.fromkeys(key[0] for key in keys))
    result.notes.append(
        f"like-for-like over {len(keys)} cell(s): {len(cases)} case(s) x {len(_ordered_stages(keys))} stage(s)"
    )
    if new:
        result.notes.append(f"{len(new)} cell(s) not in the baseline, not compared: {_key_list(new)}")
    if absent:
        result.notes.append(f"{len(absent)} baseline cell(s) not in this run, left out of every aggregate")

    def score_var(key: Key) -> float | None:
        return diff_variance(values_of(base, key, "scores"), values_of(now, key, "scores"))

    def unit(level: str, label: str, weights: dict[Key, float]) -> Comparison:
        before, after = _weighted(weights, base, "scores"), _weighted(weights, now, "scores")
        return Comparison(level, label, before, after, floor, k, combined_se(weights, score_var))

    if keys:
        result.contract.append(unit("overall", "overall", overall_weights(keys)))
        for stage in _ordered_stages(keys):
            result.contract.append(unit("stage", stage, stage_weights(keys, stage)))
        for key in keys:
            result.contract.append(unit("cell", f"{key[0]}/{key[1]}", {key: 1.0}))

    conf_keys = [key for key in keys_of(now, "confidence") if values_of(base, key, "confidence")]
    if not conf_keys and keys_of(now, "confidence"):
        result.confidence_notes.append("not in the baseline yet - re-baseline to start tracking")

    def conf_var(key: Key) -> float | None:
        return diff_variance(values_of(base, key, "confidence"), values_of(now, key, "confidence"))

    for stage in _ordered_stages(conf_keys):
        weights = stage_weights(conf_keys, stage)
        before, after = _weighted(weights, base, "confidence"), _weighted(weights, now, "confidence")
        result.confidence.append(
            Comparison("stage", stage, before, after, confidence_floor, k, combined_se(weights, conf_var))
        )


def _compare_aggregates(
    result: CheckResult,
    summary: dict[str, Any],
    baseline: dict[str, Any],
    floor: float,
    k: float,
    confidence_floor: float,
) -> None:
    """Against a format-1 baseline, which holds aggregates only.

    Those aggregates cannot be re-cut, so a unit is compared only where this run
    covers exactly what the stored number covers. Variance comes from this run's
    repeats, if any; a format-1 baseline is always a single run.
    """
    now: Cells = summary.get("cells") or {}
    now_cases, base_cases = set(summary.get("per_case") or {}), set(baseline.get("per_case") or {})
    now_stages, base_stages = set(summary.get("per_stage") or {}), set(baseline.get("per_stage") or {})
    # A hand-built summary without per_case cannot say which cases it covers;
    # it is taken at its word, as before.
    same_cases = not now_cases or not base_cases or now_cases == base_cases
    same_stages = now_stages == base_stages

    def variance_of(metric: str) -> Callable[[Key], float | None]:
        return lambda key: diff_variance(None, values_of(now, key, metric))

    def se(weights: dict[Key, float], metric: str) -> float | None:
        return combined_se(weights, variance_of(metric))

    if "overall" in summary and "overall" in baseline:
        overall_se = se(overall_weights(keys_of(now)), "scores")
        if same_stages and same_cases:
            before = baseline["overall"]
        elif same_stages and now_cases <= base_cases:
            # Overall is the mean of case means and every baseline case mean
            # covers the same stages, so the selected cases re-aggregate exactly.
            before = statistics.mean(baseline["per_case"][c] for c in now_cases)
        else:
            before = None
            result.notes.append("overall: not compared, this run covers other stages or cases than the stored overall")
        if before is not None:
            result.contract.append(Comparison("overall", "overall", before, summary["overall"], floor, k, overall_se))

    for stage, score in (summary.get("per_stage") or {}).items():
        before = (baseline.get("per_stage") or {}).get(stage)
        if before is None:
            result.notes.append(f"{stage}: new stage, {score:.1f}")
        elif not same_cases:
            result.notes.append(f"{stage}: not compared, the baseline's stage mean covers other cases")
        else:
            stage_se = se(stage_weights(keys_of(now), stage), "scores")
            result.contract.append(Comparison("stage", stage, before, score, floor, k, stage_se))

    current = summary.get("per_stage_confidence") or {}
    recorded = baseline.get("per_stage_confidence") or {}
    if current and not recorded:
        result.confidence_notes.append("not in the baseline yet - re-baseline to start tracking")
        return
    for stage, score in current.items():
        before = recorded.get(stage)
        if before is None:
            result.confidence_notes.append(f"{stage}: new, {score:.1f}")
        elif not same_cases:
            result.confidence_notes.append(f"{stage}: not compared, the baseline's stage mean covers other cases")
        else:
            conf_se = se(stage_weights(keys_of(now, "confidence"), stage), "confidence")
            result.confidence.append(Comparison("stage", stage, before, score, confidence_floor, k, conf_se))


def compare_summaries(
    summary: dict[str, Any],
    baseline: dict[str, Any],
    *,
    floor: float = REGRESSION_TOLERANCE,
    k: float = DEFAULT_K,
    confidence_floor: float = CONFIDENCE_TOLERANCE,
) -> CheckResult:
    """Apply the regression rule to every comparable unit. Prints nothing."""
    result = CheckResult()
    if summary.get("cells") and baseline.get("cells"):
        _compare_cells(result, summary["cells"], baseline["cells"], floor, k, confidence_floor)
    else:
        if not baseline.get("cells"):
            result.notes.append(
                "the baseline has no per-cell scores (format 1), so only units covering exactly what a "
                "stored aggregate covers are compared; re-baseline to compare any subset like-for-like"
            )
        _compare_aggregates(result, summary, baseline, floor, k, confidence_floor)
    return result


def report_check(
    result: CheckResult,
    *,
    floor: float,
    k: float,
    confidence_floor: float,
    out: Callable[[str], None] = print,
) -> None:
    out(
        f"  rule: a drop regresses when it exceeds max(floor, {k:g} x SE); "
        f"floor {floor:g}, confidence floor {confidence_floor:g}"
    )
    for note in result.notes:
        out(f"  note: {note}")
    if result.single_run:
        out(f"  {NOISY_SINGLE_RUN_WARNING}")

    for c in result.contract:
        if c.level != "cell":
            out(f"  {c.line()}")
    cells = [c for c in result.contract if c.level == "cell"]
    if cells:
        bad = [c for c in cells if c.regressed]
        out(f"  cells: {len(cells)} compared, {len(bad)} regressed")
        for c in bad:
            out(f"    {c.line()}")

    if result.confidence or result.confidence_notes:
        out("")
        out("  confidence:")
        for note in result.confidence_notes:
            out(f"    {note}")
        for c in result.confidence:
            out(f"    {c.line()}")

    if result.regressions:
        out("")
        out(f"  {len(result.regressions)} regression(s): " + ", ".join(c.label for c in result.regressions))
