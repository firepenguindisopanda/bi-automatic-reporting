"""Compare eval runs made with different models, stage by stage.

Each input is a results JSON from ``evals.run --model M --label L``. Runs are
grouped by the model their stages used, so several files for one model (say,
two repeats run separately) pool together.

    uv run python -m evals.models evals/results/model-*.json
    uv run python -m evals.models evals/results/model-*.json --out evals/results/models.md

Per stage and model it reports:

  ok       stages that returned output, out of the stages the run should have
           reached. A stage after a failed one never runs, so it counts as missed.
  score    contract completeness less 15 per failed assertion (as evals.run).
  critic   the critic's confidence. Completeness barely separates models; this
           is the signal that does.
  quality  mean of score and critic, with a missed stage counting 0 for both.
           The ranking key.
  time     median seconds per stage, including the critic call.

Compare runs made with the same critic and the same retry threshold; the
header lists both. Run candidates with CRITIC_RETRY_THRESHOLD=0 so a re-run
does not hide a weak first answer.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PENALTY = 15.0


def _model_of(run: dict[str, Any]) -> str:
    models = set((run.get("models") or {}).values())
    return models.pop() if len(models) == 1 else "+".join(sorted(models)) or "unknown"


def _score(stage: dict[str, Any]) -> float:
    if not stage.get("ok") or stage.get("completeness") is None:
        return 0.0
    return max(0.0, float(stage["completeness"]) - PENALTY * len(stage.get("assertion_failures") or []))


def collect(paths: list[Path]) -> tuple[dict[str, dict[str, dict[str, list[float]]]], list[str], set[str]]:
    """model -> stage -> metric -> values, plus the stage order and the critics seen."""
    table: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    order: list[str] = []
    critics: set[str] = set()
    for path in paths:
        run = json.loads(path.read_text(encoding="utf-8"))
        model = _model_of(run)
        stages = list((run.get("models") or {}).keys())
        order = order or stages
        critics.add(f"{run.get('critic')} (retry below {run.get('critic_retry_threshold')})")
        for case in run["cases"]:
            seen = {s["stage"]: s for s in case["stages"]}
            for key in stages:
                cell = table[model][key]
                stage = seen.get(key)
                ok = bool(stage and stage.get("ok"))
                cell["expected"].append(1)
                cell["ok"].append(1 if ok else 0)
                score = _score(stage) if stage else 0.0
                confidence = (stage or {}).get("confidence")
                cell["score"].append(score)
                if ok and confidence is not None:
                    cell["critic"].append(confidence)
                cell["quality"].append((score + (confidence or 0.0)) / 2 if ok else 0.0)
                if stage and stage.get("duration_ms"):
                    cell["seconds"].append(stage["duration_ms"] / 1000)
                if stage:
                    cell["assert_fail"].append(len(stage.get("assertion_failures") or []))
    return table, order, critics


def _mean(values: list[float]) -> str:
    return f"{statistics.mean(values):5.1f}" if values else "    -"


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):5.1f}" if values else "    -"


def render(table: dict[str, dict[str, dict[str, list[float]]]], order: list[str], critics: set[str]) -> str:
    lines = ["# Model comparison", "", f"Critic: {', '.join(sorted(critics))}", ""]

    def row(model: str, cells: list[dict[str, list[float]]]) -> str:
        expected = sum(sum(c["expected"]) for c in cells)
        ok = sum(sum(c["ok"]) for c in cells)
        fails = sum(sum(c["assert_fail"]) for c in cells)
        merged = {k: [v for c in cells for v in c[k]] for k in ("score", "critic", "quality", "seconds")}
        return (
            f"| {model} | {ok}/{expected} | {_mean(merged['score'])} | {_mean(merged['critic'])} "
            f"| **{_mean(merged['quality'])}** | {fails} | {_median(merged['seconds'])} |"
        )

    header = "| model | ok | score | critic | quality | assertion fails | median s |\n|---|---|---|---|---|---|---|"

    def by_quality(stage_keys: list[str]) -> list[str]:
        def key(model: str) -> float:
            values = [v for s in stage_keys for v in table[model][s]["quality"]]
            return statistics.mean(values) if values else -1.0

        return sorted(table, key=key, reverse=True)

    lines += ["## All stages", "", header]
    lines += [row(m, [table[m][s] for s in order]) for m in by_quality(order)]
    for stage in order:
        lines += ["", f"## {stage}", "", header]
        lines += [row(m, [table[m][stage]]) for m in by_quality([stage])]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", nargs="+", type=Path, help="results JSON files from evals.run")
    parser.add_argument("--out", type=Path, help="also write the markdown here")
    args = parser.parse_args(argv)
    table, order, critics = collect(args.results)
    if not table:
        print("No runs found.")
        return 2
    report = render(table, order, critics)
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
