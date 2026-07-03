# Task 9 Investigation: `invoke_structured` returns None from ThreadPoolExecutor

## Current behavior

`BIPipeline.submit()` → `loop.run_in_executor(pool, _run_sync, ...)` → thread creates new event loop → `_run_thlocal` runs async calling sync `invoke_structured()` → **returns None silently**.

Known to work:
- Sync `invoke_structured()` from main thread → works
- Sync `invoke_structured()` from bare `threading.Thread()` → works
- Async `ainvoke_structured()` from main event loop → works

Known to fail:
- Sync `invoke_structured()` from ThreadPoolExecutor + `new_event_loop()` + `run_until_complete()` chain → None

## Hypothesis

`ChatNVIDIA`'s internal httpx client reads `asyncio.get_event_loop()` during `invoke()`. When called inside `run_until_complete()` (where the event loop is running), httpx detects a running loop and either errors silently or routes through its async path incorrectly, producing `None`.

## Step 1: Root cause isolation

Create `tests/repro_thread_loop.py` — minimal reproduction of the exact thread+loop nesting:

```python
"""
Reproduce the pipeline's threading pattern in isolation.

Tests combinations:
  A. Sync invoke from bare thread
  B. Sync invoke from ThreadPoolExecutor
  C. Sync invoke from thread + new_event_loop + run_until_complete
  D. Sync invoke from thread + new_event_loop(no run_until_complete)
  E. ainvoke from thread + new_event_loop + run_until_complete
"""
```

Run each combination and compare. If C fails but A and B pass, the `run_until_complete` context is the trigger. If D passes but C fails, it's specifically `run_until_complete` running. If E also fails, it's the thread+loop combo itself.

## Step 2: Fix — primary approach (fully async pipeline)

Move from threadpool-async hybrid to a purely async background task on the main event loop:

1. **Revert all agents to `async def`** using `await self._llm.ainvoke_structured()`
2. **`BIPipeline.submit()`** uses `asyncio.create_task(self._run(job_id, ...))` instead of `run_in_executor`
3. **`BIPipeline._run()`** is fully async, no threading

This is architecturally correct: LLM calls are I/O bound (HTTP POSTs to NVIDIA API). Async handles I/O concurrency efficiently. The original `to_thread` wrapping was a workaround for the lack of `ainvoke_structured`, which now exists.

**Risks:**
- Background task runs on the main event loop. This is fine for I/O-bound work.
- If the pipeline has CPU-heavy steps, those should still be in `to_thread` — but all current steps are I/O (HTTP scrape + LLM calls + file writes).
- `asyncio.create_task` is fire-and-forget — FastAPI's lifespan must wait for pending tasks on shutdown.

**Acceptance criteria:**
- [ ] Agents use `ainvoke_structured`, pipeline runs via `create_task`
- [ ] E2E smoke test submits URL, polls, downloads PDF/DOCX
- [ ] Status endpoint returns accurate progress during execution

## Step 3: Fallback (subprocess)

If async pipeline doesn't work (e.g., httpx client state corruption across concurrent tasks):

1. Create `app/orchestrator/worker.py` — standalone script that takes `job_id` as CLI arg
2. `BIPipeline.submit()` spawns `subprocess.Popen([sys.executable, "-m", "app.orchestrator.worker", job_id])`
3. Worker reads job from SQLite, runs pipeline, writes results back
4. Completely isolated process heap — no thread-safety issues

**Trade-off:** ~200ms subprocess spawn overhead per job.

## Step 4: Document root cause

Write finding to AGENTS.md so future sessions know:
- Root cause (once confirmed)
- Why the thread pool + event loop combo fails
- Which fix was chosen and why

## Files likely touched

| File | Change |
|------|--------|
| `tests/repro_thread_loop.py` | Isolation reproduction script (delete after investigation) |
| `app/orchestrator/bi_pipeline.py` | Rewrite `submit()`/`_run()` to use `create_task` |
| `app/analysis/agents.py` | Convert all agents back to `async def` with `ainvoke_structured` |
| `app/llm/client.py` | No changes needed (ainvoke_structured already exists) |
| `app/orchestrator/worker.py` | New file — only if fallback needed |
| `app/main.py` | Maybe: lifespan awaits pending tasks |
