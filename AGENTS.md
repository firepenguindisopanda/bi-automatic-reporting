# AGENTS.md - Project Context

## Project Info

| Field | Value |
|-------|-------|
| Type | Python backend + React frontend |
| Backend | FastAPI, Python 3.13+, managed with uv (`pyproject.toml` + `uv.lock`) |
| Frontend | React 19, TypeScript, Vite 8, Tailwind CSS v4 (`frontend/`) |
| LLM | NVIDIA NIM via `langchain_nvidia_ai_endpoints` (async only) |
| Storage | PostgreSQL (`DATABASE_URL`) or SQLite (`JOBS_DB`) |

## Where things live

| Path | What |
|------|------|
| `app/pipeline/stages.py` | The stage registry: slugs, order, labels, job/stage statuses. The only stage list. |
| `app/analysis/specs.py` | One `AgentSpec` per LLM stage: model, prompt, output model, contract, critic source. |
| `app/analysis/prompts.py` | System prompts, each with a `*_V` version recorded on every stage row. |
| `app/analysis/runner.py` | `AgentRunner`: repair ladder, transport retries, failover to `AGENT_FALLBACK_MODEL`, truncation flag, critic re-run. |
| `app/analysis/critic.py` | The LLM critic (confidence score). |
| `app/pipeline/contracts.py` | Deterministic completeness scoring. Scored, never enforced. |
| `app/orchestrator/bi_pipeline.py` | BI job: scrape, analyse, assemble, render, deliver; retry/resume; resend. |
| `app/orchestrator/market_research.py` | Market research job: macro, sizing, resources, render. |
| `app/orchestrator/stage_recorder.py` | Ordered, buffered stage writes. |
| `app/orchestrator/schema.py` | Base DDL plus numbered `MIGRATIONS`. |
| `app/api/routes.py`, `app/api/progress.py` | HTTP API and the SSE stream. |
| `app/pipeline/events.py` | In-process change notifier that wakes SSE streams. |
| `app/email/policy.py` | Recipient allowlist and send caps. |
| `app/report/generator.py` | PDF (WeasyPrint) and DOCX rendering; templates in `app/report/templates/`. |
| `app/branding.py` | Brand name, byline, palette and house style, shared by the email, the PDF and the From line. |
| `evals/` | Offline eval harness: saved fixtures, real agents, baseline comparison. |

## Invariants - keep these true

- **The report is assembled from scored stages.** The report writer returns only `ReportSynthesis`
  (executive summary, recommendations, risks). `build_report()` combines it with the profile,
  market, competitive and SWOT stage outputs. Never let a stage re-generate another stage's section.
- **Model output is untrusted.** It comes from a page the submitter chose. Report templates
  autoescape (`Environment(autoescape=True)`), WeasyPrint renders through a fetcher that serves only
  files under `app/report/assets/` (`write_pdf`), and email text goes through `display_company_name`
  or `display_text` and `html.escape`.
- **No em dashes in anything a reader sees.** House style for the brand: template copy uses none, and
  model text passes through `without_em_dashes` (the Jinja `finalize` hook and the email helpers).
- **Every send passes the recipient policy** (`app/email/policy.py`): allowlist, per-recipient daily
  cap, per-job delivery cap. `OWNER_EMAILS` are exempt from all three, never from the rate limit. Submit, resend and retry are rate limited (`is_rate_limited` in `app/main.py`).
- **A job's terminal status is written last.** Stage writes are flushed first, so a client that stops
  at the terminal status has seen every stage. Delivery returns the status; it does not write it.
- **No stage is left `running`.** `StageError` names its stage; anything else goes through
  `recorder.fail_running()`.
- **Bump a prompt's `*_V` whenever its text changes**, so eval results stay attributable.
- **Schema changes are new `Migration` entries**, idempotent, never edits to old ones.
- **Single process.** The job queue and the SSE notifier live in memory. Startup recovery marks every
  unfinished job interrupted; with more than one process that would interrupt live jobs.

## Running things

```bash
uv sync                                        # install from uv.lock
uv run pytest tests/ -q                        # never calls a model
uv run ruff check . && uv run mypy app evals
cd frontend && npm ci && npx tsc -b && npm run lint && npm test && npm run build
./scripts/check-design-rules.sh
uv run python -m evals.run --stages profile --repeat 3   # real model calls - costs quota
```

- After changing dependencies: `uv lock`, then re-export `requirements.txt` (command in README.md).
  CI fails if either is stale.
- WeasyPrint needs Pango/Cairo. It is imported lazily, so the API and tests import without it;
  only PDF rendering (and two tests, which skip) needs it. On Windows, run those in Docker.
- Tests isolate themselves via env defaults in `tests/conftest.py` (in-memory SQLite, console email,
  high rate and recipient limits). Keep new tests off the network and off real models.

## History worth knowing

- The sync `.invoke()` path was removed: ChatNVIDIA returned truncated bodies when called from
  worker threads. Everything is `ainvoke`, and structured output is parsed from raw JSON replies
  (`build_json_messages` / `parse_json_response`), not `with_structured_output`.
- Models were swapped on 2026-08-26 when NVIDIA retired the originals, and re-chosen on 2026-10-10
  by an eval sweep across 16 sites (`evals/results/models-2026-10-10.md`; reasoning in `app/config.py`).
  To compare models again: `evals.run --model M` per candidate, then `evals.models`.
- The critic used to see 4,000 characters of the page while analysts saw up to 15,000, so it scored
  correct facts from further down as fabricated. Fixed in critic/2; older confidence numbers are lower
  for that reason, not because the analysis was worse.
