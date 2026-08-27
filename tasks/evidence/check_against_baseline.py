import json
from pathlib import Path

from evals.run import compare_to_baseline

for label, path in (("run1 (pre-repair)", "evals/results/rebaseline-2026-08-26.json"),
                    ("run2 (pre-repair)", "evals/results/rebaseline-run2.json"),
                    ("run3 (the baseline itself)", "evals/results/rebaseline-run3-post-repair.json")):
    summary = json.loads(Path(path).read_text())["summary"]
    print(f"\n##### {label}")
    rc = compare_to_baseline(summary, Path("evals/baseline.json"))
    print(f"  --check exit code: {rc}  ({'PASS' if rc == 0 else 'FAIL'})")
