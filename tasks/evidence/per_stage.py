"""Per-stage means - the level --check actually compares - across two runs."""
import json
import statistics
import sys
from pathlib import Path

TOL = 5.0


def per_stage(path: Path) -> tuple[dict, dict, float]:
    d = json.loads(path.read_text())
    comp: dict[str, list[float]] = {}
    conf: dict[str, list[float]] = {}
    for case in d["cases"]:
        for st in case["stages"]:
            if st.get("completeness") is not None:
                comp.setdefault(st["stage"], []).append(st["completeness"])
            if st.get("confidence") is not None:
                conf.setdefault(st["stage"], []).append(st["confidence"])
    return (
        {k: statistics.mean(v) for k, v in comp.items()},
        {k: statistics.mean(v) for k, v in conf.items()},
        d.get("summary", {}).get("overall", float("nan")),
    )


NAME_A, NAME_B = Path(sys.argv[1]).stem, Path(sys.argv[2]).stem
a_comp, a_conf, a_all = per_stage(Path(sys.argv[1]))
b_comp, b_conf, b_all = per_stage(Path(sys.argv[2]))

print(f"overall: {a_all:.1f} -> {b_all:.1f}  ({b_all - a_all:+.1f})\n")
for label, a, b in (("COMPLETENESS", a_comp, b_comp), ("CONFIDENCE", a_conf, b_conf)):
    print(f"=== {label} per-stage mean, {NAME_A} -> {NAME_B} ===")
    breaches = 0
    for stage in ("profile", "market", "competitive", "swot", "marketing", "report"):
        if stage not in a or stage not in b:
            continue
        d = b[stage] - a[stage]
        flag = "  <-- would fail --check" if abs(d) > TOL else ""
        breaches += abs(d) > TOL
        print(f"  {stage:12} {a[stage]:5.1f} -> {b[stage]:5.1f}  ({d:+5.1f}){flag}")
    print(f"  {breaches} stage(s) exceed REGRESSION_TOLERANCE={TOL} "
          f"with no code change at all\n")
