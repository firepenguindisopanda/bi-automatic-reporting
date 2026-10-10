# Business Intelligence System

Automated BI platform: scrapes websites, runs AI analysis (market, competitive, SWOT, marketing), generates PDF + DOCX reports, and emails them.

## Architecture

```
POST /api/bi/submit              → Submit URL + email → returns job_id
GET  /api/bi/progress/{id}       → Full stage array, timings, scores, ETA
GET  /api/jobs/{id}/events       → The same payload as server-sent events
POST /api/bi/{id}/retry          → Resume a failed/interrupted job
POST /api/bi/{id}/resend         → Send a finished report again (limited)
GET  /api/bi/download/{id}       → Download report (?format=pdf|docx)
GET  /api/pipelines/{kind}/stages → The stage registry
GET  /api/health, /api/health/ready
```

Pipeline: **Scrape** → **Business Profile** → (**Market** → **Competitive** → **SWOT**
alongside **Marketing**) → **Report** → **Render** → **Email**

Every stage records its duration, model, attempt count, a deterministic
completeness score and an optional LLM confidence score. The report the reader
gets is assembled from those scored stage outputs; the report writer adds only
the executive summary, recommendations and risks. Progress streams over SSE at
`/api/jobs/{id}/events` (woken by writes, not a poll), with polling at
`/api/bi/progress/{id}` as a fallback. A failed or interrupted job can be resumed:
stages whose stored output is still valid are reused, not re-run.

Backend: Python 3.13+, FastAPI, PostgreSQL or SQLite (numbered migrations in
`app/orchestrator/schema.py`), NVIDIA NIM via LangChain.
Frontend: React 19, TypeScript, Vite, Tailwind CSS v4.

## Quick Start

```bash
cp .env.example .env
# Set NVIDIA_API_KEY (required) and an email provider

uv sync                                   # dependencies come from uv.lock
uv run uvicorn app.main:app --reload
# → http://localhost:8000/docs

cd frontend && npm install && npm run dev
# → http://localhost:5173
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `NVIDIA_API_KEY` | ✅ | - | NVIDIA NIM API key |
| `NVIDIA_MODEL` | - | `openai/gpt-oss-20b` | Default LLM; per-stage `AGENT_MODEL_*` override it |
| `EMAIL_PROVIDER` | - | `smtp` | `smtp`, `resend`, `sendgrid`, `console`, `file` |
| `SMTP_HOST` / `SMTP_PORT` | - | -, `587` | SMTP endpoint |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | - | - | SMTP credentials |
| `SMTP_SECURITY` | - | `starttls` | `starttls`, `ssl`, `none` |
| `SENDGRID_API_KEY` | - | - | Only when `EMAIL_PROVIDER=sendgrid` |
| `RESEND_API_KEY` | - | - | Only when `EMAIL_PROVIDER=resend` |
| `ENABLE_STAGE_CRITIC` | - | `true` | LLM review pass per stage |
| `CRITIC_RETRY_THRESHOLD` | - | `60` | Below this, a stage re-runs once |
| `JOB_CONCURRENCY` | - | `1` | Jobs run at once (keep at 1 on a small host) |
| `MAX_RETRIES_PER_JOB` | - | `2` | Resumes allowed for one failed/interrupted job |
| `RECIPIENT_ALLOWLIST` | - | empty | Addresses/domains reports may go to, separated by commas or semicolons; empty allows any |
| `MAX_REPORTS_PER_RECIPIENT_PER_DAY` | - | `5` | Per-address cap across all jobs and resends |
| `MAX_DELIVERIES_PER_JOB` | - | `3` | Sends allowed for one report, resends included |
| `RETENTION_DAYS` | - | `0` | Delete finished jobs and report files after this many days; `0` keeps all |
| `FROM_EMAIL` | - | `reports@bisystem.com` | Sender email |
| `REPORT_OUTPUT_DIR` | - | `reports` | Report output directory |
| `JOBS_DB` | - | `jobs.db` | SQLite database path (ignored when `DATABASE_URL` is set) |
| `DATABASE_URL` | - | - | PostgreSQL URL; empty falls back to SQLite |
| `RATE_LIMIT_MAX_REQUESTS` | - | `10` | Submits, resends and retries per window, per caller |
| `RATE_LIMIT_WINDOW` | - | `60` | Rate limit window, seconds |
| `TRUST_PROXY_HEADERS` | - | `true` | Identify callers via `X-Real-IP`/`X-Forwarded-For` |
| `CORS_ORIGINS` | - | `localhost:5173,4173` | Comma-separated allowed origins |
| `SCRAPER_BLOCK_PRIVATE_NETWORKS` | - | `true` | Refuse to fetch private/loopback/CGNAT addresses |
| `SCRAPER_MAX_REDIRECTS` | - | `5` | Redirect hops; each is re-validated |

### Abuse limits on outbound mail

Nothing authenticates a submitter, and the submitter chooses the recipient. The
recipient is validated as an address, can be restricted with
`RECIPIENT_ALLOWLIST`, and is capped per day across all jobs; resend is capped
per job and rate limited. The company name - model output derived from the
scraped page - is stripped of control characters and links before it reaches a
subject line, and escaped in the HTML body.

### Report rendering

Report templates autoescape every value, and WeasyPrint renders with a URL
fetcher that refuses all fetches, so text injected through a scraped page can
neither add markup to the PDF nor make the renderer fetch a URL or local file.

### Outbound fetch policy

The scraper fetches user-supplied URLs from inside the deployment network. With
`SCRAPER_BLOCK_PRIVATE_NETWORKS=true` (the default) it refuses loopback,
private, link-local, multicast, reserved and carrier-grade-NAT addresses —
including the `100.64.0.0/10` range Tailscale uses — and re-checks after every
redirect. Turn it off only on a host that runs nothing else.

## Evals

Measures whether a prompt or model change helped, against saved fixtures so
scraping never varies. See [evals/README.md](evals/README.md).

```bash
uv run python -m evals.run --stages profile --repeat 3     # fast
uv run python -m evals.run --repeat 2 --check              # fail on regression
```

The evals workflow runs the profile stage three times on pull requests that
touch prompts, specs, contracts or the LLM client, and the full suite nightly;
both fail on a regression larger than run-to-run noise.

## Deployment

Target is an IBM LinuxONE VM — **s390x**, big-endian. Two steps, in this order:

```bash
cd frontend && npm ci && npm run build   # x86_64: produces frontend/dist
cd .. && docker compose up -d --build    # image COPYs frontend/dist into ./static
```

The frontend is deliberately **not** built inside the image. Vite 8 bundles with
rolldown, whose s390x native binding segfaults, and no Node base image builds it
on that architecture. `frontend/dist` is static assets with no architecture in
them, so it is built on x86_64 and copied in. If you skip step one, the `COPY`
fails and the build stops — that is the intended failure.

CI runs on x86_64 and never builds for s390x, so a green CI run is not evidence
the image builds. See `SPEC.md` §4 for the wheel and native-binding findings.

## Tests

```bash
uv run pytest tests/ --cov=app -v          # backend; never calls a model
cd frontend && npm test                    # vitest
cd frontend && npm run test:e2e            # Playwright, every /api call mocked

# Smoke test against a running server (requires NVIDIA_API_KEY)
uv run uvicorn app.main:app --port 8001 &
uv run python -m tests.smoke_test
```

## Quality

```bash
uv run ruff check .
uv run mypy app evals
cd frontend && npx tsc -b && npm run lint
./scripts/check-design-rules.sh            # no gradients, radii 4-6px, tokens not literals
```

## Dependencies

`pyproject.toml` is the source of truth and `uv.lock` pins it. `requirements.txt`
is exported from the lock for the Docker build - don't edit it by hand. After
changing dependencies:

```bash
uv lock
uv export --locked --no-dev --no-hashes --format requirements-txt --no-header -o requirements.txt
```

CI fails if the lock or the export is out of date.
