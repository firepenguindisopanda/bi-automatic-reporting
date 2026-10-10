"""evals.models: pooling runs by model and ranking them per stage. No model calls."""

import json
from pathlib import Path

from evals.models import collect, main, render


def _run(path: Path, model: str, stages: list[dict]) -> Path:
    path.write_text(json.dumps({
        "models": {"profile": model, "market": model},
        "critic": "judge", "critic_retry_threshold": 0,
        "cases": [{"case": "acme", "stages": stages}],
    }))
    return path


def _ok(stage: str, completeness: float, confidence: float, fails: int = 0) -> dict:
    return {"stage": stage, "ok": True, "completeness": completeness, "confidence": confidence,
            "duration_ms": 2000, "assertion_failures": ["x"] * fails}


class TestCollect:
    def test_runs_of_one_model_pool(self, tmp_path: Path) -> None:
        a = _run(tmp_path / "a.json", "m1", [_ok("profile", 100, 90), _ok("market", 100, 70)])
        b = _run(tmp_path / "b.json", "m1", [_ok("profile", 100, 80), _ok("market", 100, 60)])
        table, order, critics = collect([a, b])
        assert order == ["profile", "market"]
        assert table["m1"]["profile"]["critic"] == [90, 80]
        assert critics == {"judge (retry below 0)"}

    def test_a_stage_never_reached_counts_as_missed(self, tmp_path: Path) -> None:
        failed = {"stage": "profile", "ok": False, "error": "boom"}
        run = _run(tmp_path / "a.json", "m1", [failed])
        table, _, _ = collect([run])
        assert table["m1"]["market"]["ok"] == [0]
        assert table["m1"]["market"]["quality"] == [0.0]

    def test_assertion_failures_cost_score(self, tmp_path: Path) -> None:
        run = _run(tmp_path / "a.json", "m1", [_ok("profile", 100, 90, fails=2), _ok("market", 100, 90)])
        table, _, _ = collect([run])
        assert table["m1"]["profile"]["score"] == [70.0]


class TestRender:
    def test_better_model_ranks_first(self, tmp_path: Path) -> None:
        weak = _run(tmp_path / "w.json", "weak", [_ok("profile", 100, 40), _ok("market", 80, 40)])
        strong = _run(tmp_path / "s.json", "strong", [_ok("profile", 100, 95), _ok("market", 100, 90)])
        report = render(*collect([weak, strong]))
        section = report.split("## market")[1]
        assert section.index("| strong |") < section.index("| weak |")

    def test_cli_writes_the_report(self, tmp_path: Path) -> None:
        run = _run(tmp_path / "a.json", "m1", [_ok("profile", 100, 90), _ok("market", 100, 90)])
        out = tmp_path / "models.md"
        assert main([str(run), "--out", str(out)]) == 0
        assert "## profile" in out.read_text()
