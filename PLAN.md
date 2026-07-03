# Implementation Plan: Automated Business Intelligence System

## Overview

A standalone service at `/home/nicho_unix/code/business-intelligence/`:

> User submits a URL + email → system scrapes the site → AI analyzes business profile, market, competitive positioning, and SWOT → generates a professional report (PDF + DOCX) aligned with the marketing/SEO report template → emails it to the user's inbox. A React 19 + Vite + Tailwind frontend wraps the whole flow.

### The key reframe (vs. the prior plan)

The prior plan assumed a greenfield build. **It is not.** The reference project at `/home/nicho_unix/code/interactive_nvidia_nims/multi-sotware-team/backend/` already ships a complete, working BI backend that is **fully self-contained from the spec-generator system** - BI imports nothing from `app/agents/*`, `app/orchestrator/pipeline.py`, `app/orchestrator/context.py`, `app/models/artifacts.py`, `app/models/requests.py`, or `app/prompts/templates.py`.

So the real work is **port + harden + extend + frontend**, not author-from-scratch. This cuts implementation effort roughly in half and lets us spend that budget on bug fixes, security, alignment with the richer report template, and quality tooling.

**What we port (BI-only, self-contained):** `config.py`, `llm/client.py`, `models/bi.py`, `scraper/engine.py`, `analysis/agents.py`, `orchestrator/bi_pipeline.py`, `report/generator.py`, `email/service.py`, the 3 BI API routes + `/health`, `tests/conftest.py`, `tests/test_llm_client.py`, `requirements.txt`, `.env.example`.

**What we drop (spec-generator):** all of `app/agents/*` (12 files), `app/orchestrator/pipeline.py`, `app/orchestrator/context.py`, `app/models/artifacts.py`, `app/models/requests.py`, `app/prompts/templates.py`, the routes under `/api/generate`, `/api/generate/stream`, `/api/tech-stack/*`, and their tests.

> The local `business_analysis_report_template.md` is a marketing/SEO-oriented report (sections: company info, target audience personas, brand personality matrix, unique value proposition, people-also-ask, customer journey, customer persona traits, E-E-A-T signals, GEO tactics, call-to-action). The reference BI system produces a *strategic* report (business profile, market, competitive, SWOT, recommendations, risk). Task 9 resolves the divergence.

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Approach** | Port from reference, not greenfield | Reference BI backend is already working and self-contained; ~halves effort |
| **Backend framework** | FastAPI (async) | Matches reference; Pydantic v2 at boundaries |
| **LLM provider** | NVIDIA NIMs via `langchain-nvidia-ai-endpoints` | Reuse `LLMClient.invoke_structured()` verbatim |
| **Scraping** | `httpx` + `BeautifulSoup` (lxml) | Already implemented in reference; Playwright is the documented upgrade path for JS-heavy sites |
| **Report PDF** | WeasyPrint + Jinja2 inlined template | Reference output quality is good |
| **Report DOCX** | python-docx | Reference output is good |
| **Email** | SendGrid (primary) + SMTP (documented dev-only stub) | Reference has this; SMTP stays a stub, documented |
| **Job store** | SQLite via `aiosqlite` | Jobs survive restarts; no external service; async-friendly |
| **Frontend** | Fresh React 19 + Vite + TypeScript + Tailwind v4 | Standalone (reference frontend is spec-gen) |
| **Quality** | pytest, pytest-asyncio, pytest-cov, ruff, mypy | Per project `AGENTS.md` Python standards |
| **Report content model** | Strategic BI report + marketing template (Task 8/9 >> DECISION) | Resolve divergence between reference output and the local template |

## Resolved Decisions (human-approved)

- **[Q1 Report scope] = Strategic + marketing template** → Phase C runs. The system produces the reference's strategic report AND the marketing-SEO sections from `business_analysis_report_template.md` (personas, brand personality, E-E-A-T, GEO, customer journey, CTA).
- **[Q2 Job persistence] = SQLite-backed** → jobs survive restarts via `aiosqlite`. Task 6 implements a `JobStore` backed by SQLite; no in-memory dict.
- **[Q3 Email provider] = SendGrid** → keep the reference default; SMTP stays a documented dev-only fallback.

## Security Alert (do before any copy)

The reference `multi-sotware-team/.env` contains what looks like a **live NVIDIA API key** committed to disk.

- Do **NOT** copy `.env` into the new repo - copy only `.env.example`.
- **Rotate the exposed key** in your NVIDIA console immediately.
- Add `.env` and `reports/` to `.gitignore` from the first commit.

## Known issues to fix during the port (folded into tasks)

Carried over from the source audit - each is mapped to a task below:
1. `BusinessProfile.website` referenced in report template but absent from model → **Task 4**
2. `BIJobStatus.progress` field never populated; `events` list discarded → **Task 6**
3. Async BI agents call sync `invoke_structured` (block the event loop) → **Task 5**
4. `EmailService` async methods call sync `sg.send()` / `smtplib` (block) → **Task 5**
5. SMTP fallback hardcoded `localhost:25` no auth/TLS (dev-only stub) → **Task 5** (document, not fix)
6. Unsanitized filenames from company name → **Task 7**
7. In-memory `_jobs` no persistence/TTL → **Task 6** (documented) / **Q2**
8. `__import__("datetime")` inline 4× in `generator.py` → **Task 7**
9. Inline `from urllib.parse import urlparse` in scraper → **Task 4**
10. Uvicorn module path `"backend.app.main:app"` wrong for standalone layout → **Task 2**
11. Live API key in `.env` → **Security Alert above**

## Target Standalone Layout

```
business-intelligence/
  app/
    __init__.py
    main.py                 # trimmed desc, CORS, lifespan, corrected uvicorn path
    config.py               # BI-only settings (drop critic_*)
    api/
      __init__.py
      routes.py             # /api/health + /api/bi/{submit,status,download} ONLY
    llm/
      __init__.py
      client.py             # verbatim port
    models/
      __init__.py
      bi.py                 # + website field; +marketing models (Task 8 if chosen)
    scraper/
      __init__.py
      engine.py
    analysis/
      __init__.py
      agents.py             # +to_thread; +marketing agents (Task 8)
    orchestrator/
      __init__.py
      bi_pipeline.py        # +progress emission
    report/
      __init__.py
      generator.py          # sanitized filenames; hoisted imports; template fix
      templates/            # Jinja2 HTML template(s)
    email/
      __init__.py
      service.py            # sync→to_thread; document SMTP stub
  tests/
    __init__.py
    conftest.py             # MockLLM pattern port
    test_llm_client.py
    test_scraper.py
    test_analysis_agents.py
    test_report.py
    test_email.py
    test_bi_pipeline.py
    test_api.py
  frontend/                 # Phase D scaffold
    src/{App.tsx,main.tsx,pages/BIPage.tsx,components/Layout.tsx,types/api.ts}
  .env.example
  .gitignore                # .env, reports/, frontend/node_modules, venv
  requirements.txt          # +pytest-cov, ruff, mypy
  pyproject.toml            # ruff/mypy/pytest config
  README.md
  business_analysis_report_template.md  # baseline reference template
```

## Dependency Graph

```
TO_PORT (reference, self-contained) ──┐
   config.py ── llm/client.py ──┐    │
        models/bi.py ───────────┼────┤  (Task 1-4: PORT AS-IS + micro-fixes)
        scraper/engine.py ─────┤    │
        analysis/agents.py ────┤    │
        report/generator.py ───┤    │
        email/service.py ──────┤    │
        orchestrator/bi_pipeline.py ┤
        api/routes.py (BI only) ─┘
                 │
                 ▼
        HARDEN (async, security, bug fixes)  ──────► Phase B
                 │
                 ▼
   EXTEND report to marketing template  ──────────► Phase C (if Q1=b)
                 │
                 ▼
        FRONTEND (React 19 + Vite + Tailwind)  ────► Phase D
                 │
                 ▼
        QUALITY + SHIP (tests, lint, CI, docs)  ───► Phase E
```

---

## Phase A - Port & Micro-fix the Backend (verbatim where possible)

### Task 1: Repo init, security hardening, dependencies

**Description:** Create the standalone project scaffold (package dirs, `pyproject.toml`, `requirements.txt`, `.gitignore`, `.env.example`). Copy only `.env.example` (never `.env`). Add quality deps (`pytest-cov`, `ruff`, `mypy`) on top of the 16 BI deps from the reference. **Add `aiosqlite`** (Q2 decision - SQLite job store). Add a `jobs_db` config field (e.g. `jobs.db`) in `config.py`.

**Acceptance criteria:**
- [ ] `.gitignore` excludes `.env`, `reports/`, `frontend/node_modules`, `.venv`, `__pycache__`, `*.db`
- [ ] `.env.example` has `NVIDIA_API_KEY`, `NVIDIA_MODEL`, `SENDGRID_API_KEY`, `FROM_EMAIL`, `REPORT_OUTPUT_DIR`, `JOBS_DB=jobs.db`
- [ ] `.env` is **not** copied from reference
- [ ] `requirements.txt` + `pyproject.toml` install cleanly into a fresh venv (incl. `aiosqlite`)
- [ ] `venv` creatable; `pip install -r requirements.txt` succeeds

**Verification:**
- [ ] `python -m venv .venv && .venv/bin/pip install -r requirements.txt` succeeds
- [ ] `.gitignore` contains `.env` and `*.db`
- [ ] `.env` does not exist yet (use `.env.example` only)

**Dependencies:** None
**Files likely touched:** `.gitignore`, `.env.example`, `requirements.txt`, `pyproject.toml`, `app/__init__.py`, `tests/__init__.py`
**Estimated scope:** S

---

### Task 2: Port `config.py` + `main.py` (BI-only)

**Description:** Copy `app/config.py` dropping `critic_model`/`critic_threshold`. Copy `app/main.py`, trim the dual-purpose description to BI-only, fix the `__main__` uvicorn module path from `"backend.app.main:app"` to `"app.main:app"`, and add `CORSMiddleware` with explicit origins from settings (default `http://localhost:5173` for the Vite dev server).

**Acceptance criteria:**
- [ ] `Settings` exposes only BI fields (no `critic_*`)
- [ ] `uvicorn app.main:app --reload` starts without errors
- [ ] `GET /api/health` returns `{"status":"ok"}` (router not yet wired is fine in this task; health can be added in Task 10 - coordinate)
- [ ] `NVIDIA_API_KEY` loads from `.env`

**Verification:**
- [ ] `uvicorn app.main:app --reload` starts on `0.0.0.0:8000`
- [ ] `curl -s localhost:8000/api/health` → `{"status":"ok"}`

**Dependencies:** Task 1
**Files likely touched:** `app/main.py`, `app/config.py`, `.env` (your local copy, in `.gitignore`)
**Estimated scope:** S

---

### Task 3: Port `llm/client.py` (verbatim) + tests

**Description:** Copy `app/llm/client.py` verbatim (it is shared infra). Port `tests/conftest.py` (`MockLLM` + `mock_llm_client` fixture monkeypatching `client._llm`) and `tests/test_llm_client.py`.

**Acceptance criteria:**
- [ ] `LLMClient`, `invoke`, `invoke_structured`, `with_model` present with original signatures
- [ ] `pytest tests/test_llm_client.py -v` passes (no network call)
- [ ] `MockLLM` fixture reusable by later tests

**Verification:**
- [ ] `pytest tests/ -v -k llm` green

**Dependencies:** Task 2
**Files likely touched:** `app/llm/__init__.py`, `app/llm/client.py`, `tests/conftest.py`, `tests/test_llm_client.py`
**Estimated scope:** S

---

### Task 4: Port `models/bi.py` + `scraper/engine.py` (with micro-fixes)

**Description:** Port all 10 BI models verbatim. Apply micro-fixes: add a `website: str = ""` field to `BusinessProfile`. In `scraper/engine.py`, hoist the inline `from urllib.parse import urlparse` in `_extract_links` to a top-level import (issue #9). The scraper already handles HTTP/timeout/generic errors and always returns a `ScrapedContent`.

**Acceptance criteria:**
- [ ] All BI models import cleanly (`ScrapedContent`, `BusinessProfile`, `MarketAnalysis`, `CompetitiveAnalysis`, `SWOTAnalysis`, `BIReport`, `AnalysisArtifact`, `BISubmitRequest`, `BISubmitResponse`, `BIJobStatus`)
- [ ] `BusinessProfile` has a `website` field
- [ ] `BISubmitRequest` validates URL length 5..2000 and email length 5..500
- [ ] `ScraperEngine.scrape(url)` returns `ScrapedContent`, never raises
- [ ] `urlparse` import is module-level

**Verification:**
- [ ] `python -c "from app.models.bi import *; from app.scraper.engine import ScraperEngine"` succeeds
- [ ] `pytest tests/test_scraper.py -v` (stub for now; full tests in Task 11)

**Dependencies:** Task 3
**Files likely touched:** `app/models/__init__.py`, `app/models/bi.py`, `app/scraper/__init__.py`, `app/scraper/engine.py`
**Estimated scope:** M

---

### Task 5: Port `analysis/agents.py` + `email/service.py` with async hardening

**Description:** Port the 5 BI agents (`BusinessProfileAgent`, `MarketAnalysisAgent`, `CompetitiveAnalysisAgent`, `SWOTAgent`, `BIReportWriterAgent`) and `EmailService`. **Fix the blocking-event-loop bug (issues #3, #4):** the agents' `async analyze`/`write` call `invoke_structured` synchronously - wrap in `await asyncio.to_thread(self._llm.invoke_structured, ...)`. Same for `EmailService`: wrap `sg.send()` and `smtplib` calls in `to_thread`. Keep SMTP fallback as-is (issue #5) but add a module docstring noting it is a dev-only stub expecting a local MTA on `localhost:25`.

**Acceptance criteria:**
- [ ] All 5 agents wrap `invoke_structured` in `to_thread` (no event-loop blocking)
- [ ] `EmailService.send_report` wraps sync I/O in `to_thread`
- [ ] SMTP fallback is documented as dev-only
- [ ] No test hits the real NVIDIA/SendGrid APIs

**Verification:**
- [ ] grep confirms no bare `self._llm.invoke_structured(...)` without `to_thread` in agents
- [ ] `pytest tests/test_analysis_agents.py -v` (stub) and `tests/test_email.py -v` pass with `MockLLM`/mocked senders

**Dependencies:** Task 4
**Files likely touched:** `app/analysis/__init__.py`, `app/analysis/agents.py`, `app/email/__init__.py`, `app/email/service.py`
**Estimated scope:** M

---

### Task 6: Port `orchestrator/bi_pipeline.py` → SQLite-backed `JobStore`

**Description:** Port `BIPipeline` and its `BIJob` dataclass. **Replace the in-memory `self._jobs` dict with a SQLite-backed `JobStore` (Q2 decision)** using `aiosqlite`. Create a `JobStore` class (in `app/orchestrator/job_store.py`) with the schema:
```
jobs(job_id TEXT PRIMARY KEY, url TEXT, email TEXT, status TEXT,
     progress TEXT,  -- JSON array of stage events
     artifact TEXT,  -- AnalysisArtifact.model_dump_json() or NULL
     pdf_path TEXT, docx_path TEXT, error TEXT, created_at TEXT, updated_at TEXT)
```
Methods: `init()` (CREATE TABLE IF NOT EXISTS), `create(job)`, `get(job_id)`, `update(job_id, **fields)` (status, progress, artifact, paths, error, updated_at). Serialize `AnalysisArtifact` with Pydantic `model_dump_json()` / `model_validate_json()`; serialize `progress` as JSON; store paths as strings.

`BIPipeline` now: `submit()` inserts a row + fires `asyncio.create_task(self._run(job))`; `_run()` writes progress events and status transitions to the DB at each stage (fixes issue #2 - progress is now persisted and queryable); `get_job()` reads from the DB. Recommended status transitions: `pending → scraping → analyzing → complete | error`. Wrap DB init in `main.py` lifespan startup. This also resolves issue #7 (persistence across restarts).

**Acceptance criteria:**
- [ ] `jobs.db` created on startup with the schema above
- [ ] `submit` inserts a row and returns immediately
- [ ] Each pipeline stage appends a progress event **persisted to the DB**
- [ ] `get_job(job_id)` returns the job from SQLite (or `None`)
- [ ] Backend restart retains job rows (status queryable post-restart)
- [ ] Pipeline error → `status="error"`, `error` populated, partial progress retained
- [ ] No event-loop blocking along the pipeline (relies on Task 5)
- [ ] No bare `self._llm.invoke_structured` without `to_thread`

**Verification:**
- [ ] `pytest tests/test_bi_pipeline.py -v` green with a temp `jobs.db` (use `tmp_path` fixture)
- [ ] Start backend, submit a job, restart backend, `GET /api/bi/status/{id}` still returns the row

**Dependencies:** Tasks 4, 5
**Files likely touched:** `app/orchestrator/__init__.py`, `app/orchestrator/bi_pipeline.py`, `app/orchestrator/job_store.py` (new), `app/main.py` (lifespan DB init), `app/config.py` (`jobs_db` field)
**Estimated scope:** M

---

### Task 7: Port `report/generator.py` (fix templates + filenames)

**Description:** Port `ReportGenerator` (PDF via Jinja2→WeasyPrint, DOCX via python-docx). Apply fixes:
- **(issue #6)** Sanitize the company name before using it in filenames (strip path separators / control chars, collapse spaces, cap length).
- **(issue #8)** Replace inline `__import__("datetime").datetime.now()` with a normal `from datetime import datetime` import; hoist `from weasyprint import HTML` to module level (or keep lazy import only once, clearly).
- **(issue #1)** Fix `profile.website` reference - now that `BusinessProfile.website` exists (Task 4), use it.
- Move the inline `HTML_TEMPLATE` string into `app/report/templates/report.html` (Jinja2 `FileSystemLoader`) so marketing sections can be added in Phase C without editing Python.

**Acceptance criteria:**
- [ ] `generate_pdf` / `generate_docx` return valid `Path`s under `reports/`
- [ ] Filenames are safe (no `/`, no control chars, length-capped)
- [ ] `profile.website` renders the actual value, not `None`
- [ ] Jinja2 template loaded from `app/report/templates/report.html`
- [ ] PDF opens in any viewer; DOCX opens in Word/Google Docs

**Verification:**
- [ ] `pytest tests/test_report.py -v` (creates temp dir, asserts files exist + extension)
- [ ] Open one generated PDF + one DOCX manually

**Dependencies:** Task 4 (needs `BIReport`, `BusinessProfile`)
**Files likely touched:** `app/report/__init__.py`, `app/report/generator.py`, `app/report/templates/report.html`
**Estimated scope:** M

---

### Checkpoint A: Ported Backend Compiles & Runs

- [ ] `uvicorn app.main:app --reload` starts
- [ ] `GET /api/health` → 200 `{"status":"ok"}`
- [ ] `pytest tests/ -v` - all ported tests green with mocked LLM
- [ ] No spec-generator modules remain in the tree
- [ ] No bare sync LLM/email calls (grep check)

---

## Phase B - Wire the API and Smoke-test End-to-End

### Task 8: Port BI API routes (drop spec-gen) + tests

**Description:** Copy `app/api/routes.py` and **delete** all spec-generator routes (`/api/generate`, `/api/generate/stream`, `/api/tech-stack/*`) and the `_agent_to_artifact_type` helper. Keep only:
- `POST /api/bi/submit` - `body: BISubmitRequest` → `BIPipeline.submit` → `BISubmitResponse`
- `GET /api/bi/status/{job_id}` → `BIJobStatus` (now with `progress`); 404 if unknown
- `GET /api/bi/download/{job_id}?format=pdf|docx` → `FileResponse`; 404 unknown, 400 not complete
- `GET /api/health`

Rename the router tag from `["generate"]` to `["bi"]`. The module-level `_bi_pipeline = BIPipeline()` singleton stays. Register the router in `main.py` (confirm Task 2 wiring).

**Acceptance criteria:**
- [ ] Only the 4 endpoints above exist (grep confirms no `/generate` or `/tech-stack`)
- [ ] `POST /api/bi/submit` (valid URL+email) returns `job_id`; invalid → 422
- [ ] `GET /api/bi/status/{job_id}` returns `progress` list; 404 for unknown
- [ ] `GET /api/bi/download/{job_id}?format=pdf` returns 400 until status=complete
- [ ] Router tag is `bi`

**Verification:**
- [ ] `pytest tests/test_api.py -v` green (httpx `AsyncClient` against the FastAPI app)
- [ ] OpenAPI at `/docs` shows only the 4 endpoints

**Dependencies:** Tasks 2, 6, 7
**Files likely touched:** `app/api/__init__.py`, `app/api/routes.py`, `app/main.py` (router include), `tests/test_api.py`
**Estimated scope:** M

---

### Task 9: End-to-end smoke test with real LLM (manual)

**Description:** With a valid `.env` (`NVIDIA_API_KEY` set), run the full pipeline once against a real public URL via the API and confirm PDF + DOCX land in `reports/` (skip email by leaving `SENDGRID_API_KEY` empty so the SMTP fallback is exercised - requires a local MTA, or skip email verification). Capture any remaining runtime issues for a follow-up task.

**Acceptance criteria:**
- [ ] `curl -X POST .../api/bi/submit -d '{"url":"https://example.com","email":"x@y.com"}'` returns a `job_id`
- [ ] Polling `.../api/bi/status/{id}` reaches `complete` within ~60s
- [ ] `reports/` contains a `.pdf` and `.docx` for the requested company
- [ ] PDF visually inspected - sections render, SWOT table present

**Verification:**
- [ ] Manual run; attach screenshots/links to the task

**Dependencies:** Task 8 (and a rotated, valid `NVIDIA_API_KEY`)
**Files likely touched:** none (verification)
**Estimated scope:** XS

---

### Checkpoint B: Backend is Production-shaped (modulo polish)

- [ ] All tests green (`pytest tests/ -v --cov=app --cov-report=term`)
- [ ] Coverage ≥ 80% on `app/` (excluding `main.py` boilerplate)
- [ ] End-to-end smoke test passed
- [ ] No known issue from the 11-item list outstanding except documented SMTP stub
- [ ] **Review with human before Phase C** (Phase C now confirmed to run per Q1)

---

## Phase C - Align Report with the Marketing Template

### Task 10: Extend BI data models with marketing sections

**Description:** Add Pydantic v2 models that mirror `business_analysis_report_template.md` placeholders: `CompanyInfo` (client_name, country, language, website, company_description), `TargetAudiencePersonas`, `BrandPersonalityMatrix`, `UniqueValueProposition`, `PeopleAlsoAsk`, `CustomerJourney`, `CustomerPersonaTrait`, `EEATSignalIntegration`, `GEOTactic`, `CallToAction`. Compose a new `MarketingReport` model (or extend `BIReport` with these as nested fields - prefer composition to avoid breaking existing tests). Add API response model exposure only if needed.

**Acceptance criteria:**
- [ ] New models use Pydantic v2 syntax
- [ ] `BIReport` and the new marketing models coexist; existing tests still pass
- [ ] All placeholders from `business_analysis_report_template.md` map to a field

**Verification:**
- [ ] `python -c "from app.models.bi import MarketingReport"` (or chosen name) succeeds
- [ ] `pytest tests/ -v` still green

**Dependencies:** Task 4, Phase B done
**Files likely touched:** `app/models/bi.py`
**Estimated scope:** M

---

### Task 11: Add marketing analysis agents

**Description:** Add a second agent suite (mirroring the existing 5 agents' style: `LLMClient` in `__init__`, async `analyze` calling `invoke_structured` via `to_thread`, inline system+user prompts). Agents per new model above, each consuming `ScrapedContent` + relevant prior artifacts, producing the corresponding typed model. Order them logically after the strategic agents in the pipeline.

**Acceptance criteria:**
- [ ] Each new agent accepts required inputs and returns the correct model type
- [ ] No manual JSON parsing (uses `invoke_structured`)
- [ ] All wrapped in `to_thread`

**Verification:**
- [ ] `pytest tests/test_analysis_agents.py -v` covers the new agents with `MockLLM`

**Dependencies:** Task 10
**Files likely touched:** `app/analysis/agents.py`, `tests/test_analysis_agents.py`
**Estimated scope:** M

---

### Task 12: Extend the pipeline + report template

**Description:** Extend `BIPipeline._run` to invoke the new marketing agents after the strategic ones, store results in `AnalysisArtifact` (add fields), and extend the Jinja2 PDF template + python-docx builder to render all marketing sections (executive summary box, SWOT 2×2 table, brand personality matrix, customer journey, E-E-A-T bullets, GEO tactics, CTA). Ensure both PDF and DOCX cover the marketing template.

**Acceptance criteria:**
- [ ] Pipeline produces strategic + marketing artifacts in one pass
- [ ] PDF + DOCX both include all marketing sections
- [ ] Progress events include the new stages
- [ ] No regression on existing tests

**Verification:**
- [ ] `pytest tests/test_bi_pipeline.py tests/test_report.py -v` green
- [ ] Manual smoke (Task 9 steps) - PDF visually shows the marketing sections

**Dependencies:** Tasks 10, 11
**Files likely touched:** `app/orchestrator/bi_pipeline.py`, `app/models/bi.py` (`AnalysisArtifact`), `app/report/generator.py`, `app/report/templates/report.html`
**Estimated scope:** M

---

### Checkpoint C: Report matches the marketing template

- [ ] Generated PDF/DOCX contain every section in `business_analysis_report_template.md`
- [ ] All tests green; coverage ≥ 80%

---

## Phase D - Frontend (React 19 + Vite + Tailwind v4)

### Task 13: Scaffold fresh frontend

**Description:** Run `npm create vite@latest frontend -- --template react-ts` TypeScript. Add Tailwind v4 via the Vite plugin, `react-router-dom`, and a `/api` dev proxy in `vite.config.ts` pointing at `http://localhost:8000`. Verify the dev server starts and Tailwind utilities render.

**Acceptance criteria:**
- [ ] `npm run dev` serves on `http://localhost:5173`
- [ ] Tailwind classes render
- [ ] `npx tsc --noEmit` passes
- [ ] `/api/*` requests proxy to the backend

**Verification:**
- [ ] `npm run dev` + `curl localhost:5173/api/health` → proxies to backend

**Dependencies:** Phase B complete (backend reachable)
**Files likely touched:** `frontend/` scaffold, `frontend/vite.config.ts`, `frontend/src/index.css`, `frontend/package.json`
**Estimated scope:** M

---

### Task 14: Build the BI analysis page

**Description:** Build the UI: form (URL + email + submit), submitting/loading state with spinner, polling `GET /api/bi/status/{job_id}` every 3s showing stage progress, complete state with PDF/DOCX download links (hit `/api/bi/download/{job_id}?format=...`), error state with retry. Add a simple navbar with Home + "BI Analysis". Define `frontend/src/types/api.ts` mirroring the backend response models.

**Acceptance criteria:**
- [ ] Form validates URL + email before submit (client-side UX; server still the security boundary)
- [ ] Polls status every 3s until `complete`/`error`
- [ ] Shows stage progress from `BIJobStatus.progress`
- [ ] Download links appear only when complete
- [ ] `npx tsc --noEmit` + `npm run build` succeed

**Verification:**
- [ ] `npm run build` succeeds
- [ ] Manual: submit a URL through the UI, see progress, then download both files

**Dependencies:** Tasks 8, 13
**Files likely touched:** `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/pages/BIPage.tsx`, `frontend/src/components/Layout.tsx`, `frontend/src/types/api.ts`
**Estimated scope:** M

---

## Phase E - Quality, Security, Ship

### Task 15: Lint, type-check, coverage, pre-commit, CI

**Description:** Configure `ruff` (lint + format), `mypy` (strict on `app/`), `pytest` with `pytest-cov` (fail-under 80%), and a `.pre-commit-config.yaml` (ruff, mypy, end-of-file-fixer, no-large-files). Add a GitHub Actions workflow running `ruff check`, `mypy app`, `pytest --cov` on push/PR. Apply `references/security-checklist.md` (per `AGENTS.md`): restricted CORS origins, `X-Content-Type-Options`, `X-Frame-Options`, rate-limit the auth-adjacent `POST /api/bi/submit` (e.g. `slowapi` or a simple in-memory IP limiter) to deter abuse.

**Acceptance criteria:**
- [ ] `ruff check .` clean
- [ ] `mypy app` clean
- [ ] `pytest --cov=app --cov-fail-under=80` green
- [ ] Pre-commit hooks run and pass
- [ ] CI workflow file present; jobs reference the right commands
- [ ] Rate limiting active on `POST /api/bi/submit`
- [ ] CORS restricted to configured origins only

**Verification:**
- [ ] `ruff check . && mypy app && pytest --cov=app --cov-fail-under=80` all pass locally
- [ ] `pre-commit run --all-files` green
- [ ] `curl` confirms rate-limited response after threshold on `/api/bi/submit`

**Dependencies:** Phase D complete
**Files likely touched:** `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `app/main.py` (security headers), `app/api/routes.py` (rate limit), `requirements.txt`
**Estimated scope:** M

---

### Task 16: README + final end-to-end test + ship

**Description:** Write `README.md` (setup, `.env` keys, WeasyPrint system deps - libpango/libcairo, how to run backend+frontend, limitations: in-memory jobs, SMTP stub). Run the full system end-to-end (backend + frontend) against a real URL with email delivery via real SendGrid (or documented SMTP stub). Confirm all automated checks pass.

**Acceptance criteria:**
- [ ] `README.md` documents setup, env vars, system deps, run commands, known limitations
- [ ] `python -m pytest tests/ -v` all green
- [ ] `npm run build` succeeds
- [ ] Full end-to-end through the UI delivers PDF + DOCX to an inbox (SendGrid) - or documents the SMTP-stub fallback path
- [ ] No TypeScript errors

**Verification:**
- [ ] `python -m pytest tests/ -v` green
- [ ] `npm run build` green
- [ ] Manual: receive the report email with both attachments

**Dependencies:** Task 15
**Files likely touched:** `README.md`, `business_analysis_report_template.md` (unchanged baseline)
**Estimated scope:** S

---

### Checkpoint E: Shipped

- [ ] All tests, lint, types green; coverage ≥ 80%
- [ ] CI green on the default branch
- [ ] End-to-end delivery verified
- [ ] Known limitations documented (in-memory jobs, SMTP stub)
- [ ] Review with human - ready to release

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Exposed NVIDIA key in reference `.env` | **High (security)** | Do NOT copy `.env`; rotate the key; `.gitignore` from commit 1 |
| WeasyPrint system deps (libpango/libcairo) | Medium | Document OS install in README; optional Docker image |
| JS-heavy sites don't scrape well | Low | httpx/BS4 first; Playwright is documented upgrade path |
| NVIDIA rate-limits / key missing | High | `MockLLM` pattern keeps tests offline; document demo fallback |
| Async agents blocked sync LLM (port bug) | Medium | Task 5 wraps all LLM/email calls in `to_thread` |
| In-memory jobs lost on restart | Low | **Resolved by Q2** - SQLite `JobStore` (Task 6) persists across restarts |
| SMTP stub unusable in prod | Low | Immediate SendGrid path; stub is dev-only, documented |
| Marketing template scope creep (Phase C) | Medium | Confirmed scope (Q1=b); Phase C is planned, not surprise work |

## Open Questions (block where noted)

- [x] Project name → **business-intelligence**
- [x] Frontend approach → **fresh Vite + React 19 + Tailwind v4**
- [x] Approach → **port + harden + extend** (not greenfield)
- [x] **Q1 Report scope** → **Strategic + marketing template** (Phase C runs)
- [x] **Q2 Job persistence** → **SQLite-backed** (`aiosqlite`, Task 6)
- [x] **Q3 Email provider** → **SendGrid** (SMTP dev-only fallback)

---

## Effort Outlook

| Phase | Effort vs. prior greenfield plan |
|-------|----------------------------------|
| A (Port + micro-fix) | ~50% smaller - copy verbatim, apply targeted fixes |
| B (API + smoke) | Same |
| C (marketing template) | New work (optional) |
| D (Frontend) | Same |
| E (Quality/Ship) | New - adds lint/types/cov/CI per `AGENTS.md` |

---

*Plan generated. All gating questions (Q1–Q3) resolved. Port + harden (Phases A/B) can begin immediately; Phase C (marketing template) follows Phase B.*
</analysis>
</invoke>