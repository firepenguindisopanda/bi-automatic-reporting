"""Compare two eval runs cell by cell.

The question a baseline depends on: how much does an unchanged pipeline move
between runs? If that is larger than REGRESSION_TOLERANCE, a single run cannot
serve as a reference and --check fires on noise.
"""
import json
import statistics
import sys
from pathlib import Path

TOL = 5.0


def cells(path: Path) -> tuple[dict, dict]:
    data = json.loads(path.read_text())
    comp, conf = {}, {}
    for case in data["cases"]:
        for st in case["stages"]:
            key = (case["case"], st["stage"])
            if st.get("completeness") is not None:
                comp[key] = st["completeness"]
            if st.get("confidence") is not None:
                conf[key] = st["confidence"]
    return comp, conf


def report(name: str, a: dict, b: dict) -> None:
    shared = sorted(set(a) & set(b))
    deltas = [abs(a[k] - b[k]) for k in shared]
    print(f"\n=== {name}: {len(shared)} cells in both runs ===")
    if not shared:
        return
    moved = [(k, a[k], b[k]) for k in shared if abs(a[k] - b[k]) > TOL]
    print(f"  mean |delta| {statistics.mean(deltas):5.1f}   "
          f"max |delta| {max(deltas):5.1f}   "
          f"cells moving >{TOL:.0f}: {len(moved)}/{len(shared)} "
          f"({100 * len(moved) / len(shared):.0f}%)")
    for k, x, y in sorted(moved, key=lambda t: -abs(t[1] - t[2]))[:12]:
        print(f"    {k[0]:10} {k[1]:12} {x:5.1f} -> {y:5.1f}  ({y - x:+.1f})")
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    if only_a or only_b:
        print(f"  present in run1 only: {len(only_a)}   run2 only: {len(only_b)}")


r1, r2 = Path(sys.argv[1]), Path(sys.argv[2])
c1, f1 = cells(r1)
c2, f2 = cells(r2)
print(f"run1 = {r1.name}   run2 = {r2.name}")
report("completeness", c1, c2)
report("confidence", f1, f2)

for label, d in (("run1", c1), ("run2", c2)):
    print(f"\n{label} completeness distinct values: {sorted(set(d.values()))}")
