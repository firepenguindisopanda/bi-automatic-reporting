"""The Evals workflow: what triggers it, and that its inputs cannot inject shell.

Static checks on .github/workflows/evals.yml - nothing is run.
"""

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "evals.yml"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def triggers() -> dict:
    data = workflow()
    # YAML 1.1 reads a bare `on` key as the boolean True.
    return data.get("on", data.get(True))


def steps() -> list[dict]:
    return [step for job in workflow()["jobs"].values() for step in job.get("steps", [])]


def run_step(name: str) -> dict:
    return next(s for s in steps() if s.get("name") == name)


def test_it_can_still_be_run_by_hand():
    assert "workflow_dispatch" in triggers()


def test_pull_requests_run_it_only_when_analysis_code_changes():
    assert triggers()["pull_request"]["paths"] == [
        "app/analysis/**",
        "app/pipeline/contracts.py",
        "app/llm/**",
        "evals/**",
    ]


def test_it_runs_nightly():
    assert triggers()["schedule"][0]["cron"]


def test_each_event_runs_the_intended_command():
    script = run_step("Run evals")["run"]
    assert "args+=(--stages profile --repeat 3 --check)" in script
    assert "args+=(--repeat 2 --check)" in script
    assert 'uv run --no-dev python -m evals.run "${args[@]}"' in script


def test_no_expression_is_interpolated_into_a_shell_script():
    """`${{ inputs.case }}` inside `run:` is script injection; inputs go through env."""
    for step in steps():
        assert "${{" not in step.get("run", ""), step.get("name", step.get("run"))


def test_every_input_reaches_the_script_through_env():
    env = run_step("Run evals")["env"]
    for name in triggers()["workflow_dispatch"]["inputs"]:
        assert env[f"INPUT_{name.upper()}"] == f"${{{{ inputs.{name} }}}}"


def test_a_missing_key_skips_with_a_notice_rather_than_failing():
    jobs = workflow()["jobs"]
    gate = jobs["key"]["steps"][0]["run"]
    assert "::notice" in gate
    assert jobs["eval"]["needs"] == "key"
    assert jobs["eval"]["if"] == "needs.key.outputs.present == 'true'"


def test_the_summary_tolerates_a_missing_report():
    script = run_step("Publish the report to the run summary")["run"]
    assert re.search(r'if \[ -f "\$report" \]', script)


def test_the_artifact_upload_tolerates_no_files():
    upload = next(s for s in steps() if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["with"]["if-no-files-found"] == "ignore"


def test_dependencies_come_from_the_lock():
    """The same pinned set CI tests against, not a fresh resolve."""
    assert any(s.get("run") == "uv sync --locked --no-dev" for s in steps())
