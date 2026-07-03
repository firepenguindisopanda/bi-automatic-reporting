# AGENTS.md — Project Context

## Project Info

| Field | Value |
|-------|-------|
| Type | Python |
| Framework | FastAPI |
| Python Version | 3.13+ |

## Critical Bug — `invoke_structured` returns None

### Symptoms
`LLMClient.invoke_structured()` intermittently returns `None` (~60-80% failure rate) when called as the **2nd agent** in a pipeline inside a `ThreadPoolExecutor` + `new_event_loop` + `run_until_complete` context.

### Root Cause
`langchain_nvidia_ai_endpoints` ChatNVIDIA's `with_structured_output` combined with sync `.invoke()` inside a TPE thread running via `run_until_complete` causes httpx connection pool mixing between threads. The API returns HTTP 200 with a **truncated response body** (~4KB instead of ~11KB) that `with_structured_output`'s JSON parser cannot parse, returning `None`.

### Details
- **Only happens with sync `.invoke()`** — async `.ainvoke()` works 100% reliably.
- **Model-specific**: MarketAnalysisAgent as 2nd call fails ~80%; BusinessProfileAgent as 2nd call never fails.
- **Not a prompt length issue**: hardcoded long prompts still fail; short prompts succeed.
- **Only in TPE context**: same code on main thread works 100%.
- **Not deterministic**: httpx connection pool race condition — intermittent, suggests a timing/connection-reuse issue.

### Fix Applied — Full Async Pipeline
- Added `analyze_async` / `write_async` to all 6 agents (`app/analysis/agents.py`) using `ainvoke_structured`.
- Added `_run_async` pipeline runner in `app/orchestrator/bi_pipeline.py` using `asyncio.create_task`.
- Old sync methods (`analyze`, `write`, `run`, `run_with_progress`) kept as fallback.
- Removed debug logging from `app/llm/client.py:invoke_structured`.

### Implementation Detail — Raw Invoke + JSON Schema
- `ainvoke_structured` bypasses `with_structured_output` entirely; uses raw `.ainvoke()` with system prompt containing the expected JSON schema + an inline example
- `_build_example_schema()` generates the example from the Pydantic model's `model_json_schema()`:
  - Resolves `$ref` to inline defs
  - Resolves `anyOf` (nullable fields like `brand_personality`, `eeat`) and picks the non-null branch
  - Max depth 3; deeper fields show `"..."` to keep prompt short
- `_parse_json_response()` finds all JSON objects in the raw response and returns the last valid one (handles schema-echoing by the model)
- **Key insight**: Llama 3.1 8B follows the example schema literally — if the example shows `"<string>"` for a nested Object field, it outputs a string instead of a nested JSON object. So the example must exactly match the desired output structure.

### Key Files
- `app/llm/client.py` — `invoke_structured` (sync, problematic) and `ainvoke_structured` (async, safe)
- `app/analysis/agents.py` — All 6 agent classes, each with sync `analyze` + async `analyze_async`
- `app/orchestrator/bi_pipeline.py` — `_run_async` creates tasks, `_run` uses TPE (deprecated path)
- `app/routes/status.py` `app/routes/analyze.py` — API endpoints

### Testing
- `tests/test_analysis_agent.py` (async agent tests)
- `tests/test_bi_pipeline.py` (pipeline tests)
- `tests/repro_thread_loop.py` (repro — needs NVIDIA API key)
- `tests/repro_pipeline_direct.py` (direct pipeline repro)
- Run: `uv run pytest tests/ -v`
