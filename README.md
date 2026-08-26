# Business Intelligence System

Automated BI platform: scrapes websites, runs AI analysis (market, competitive, SWOT, marketing), generates PDF + DOCX reports, and emails them.

## Architecture

```
POST /api/bi/submit        → Submit URL + email → returns job_id
GET  /api/bi/status/{id}   → Poll job status + events
GET  /api/bi/download/{id} → Download report (?format=pdf|docx)
GET  /api/health           → Health check
```

Pipeline: **Scrape** → **Business Profile** → **Market Analysis** → **Competitive Analysis** → **SWOT** → **Marketing Analysis** → **Report** → **Render** → **Email**

Every stage records its duration, model, attempt count, a deterministic
completeness score and an optional LLM confidence score. Progress streams over
SSE at `/api/jobs/{id}/events`, with polling at `/api/bi/progress/{id}` as a
fallback. See [SPEC.md](SPEC.md) and [docs/email-providers.md](docs/email-providers.md).

Backend: Python 3.13+, FastAPI, SQLite (persistent job store), NVIDIA LLM via LangChain.
Frontend: React 19, Vite, Tailwind CSS v4.

## Quick Start

```bash
cp .env.example .env
# Set NVIDIA_API_KEY (required), SENDGRID_API_KEY (optional)

uv sync
uv run uvicorn app.main:app --reload
# → http://localhost:8000/docs

cd frontend && npm install && npm run dev
# → http://localhost:5173
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `NVIDIA_API_KEY` | ✅ | - | NVIDIA NIM API key |
| `NVIDIA_MODEL` | - | `meta/llama-3.1-8b-instruct` | LLM model ID |
| `EMAIL_PROVIDER` | - | `smtp` | `smtp`, `resend`, `sendgrid`, `console`, `file` |
| `SMTP_HOST` / `SMTP_PORT` | - | -, `587` | SMTP endpoint |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | - | - | SMTP credentials |
| `SMTP_SECURITY` | - | `starttls` | `starttls`, `ssl`, `none` |
| `SENDGRID_API_KEY` | - | - | Only when `EMAIL_PROVIDER=sendgrid` |
| `RESEND_API_KEY` | - | - | Only when `EMAIL_PROVIDER=resend` |
| `ENABLE_STAGE_CRITIC` | - | `true` | LLM review pass per stage |
| `CRITIC_RETRY_THRESHOLD` | - | `60` | Below this, a stage re-runs once |
| `JOB_CONCURRENCY` | - | `1` | Jobs run at once (keep at 1 on a Pi) |
| `FROM_EMAIL` | - | `reports@bisystem.com` | Sender email |
| `REPORT_OUTPUT_DIR` | - | `reports` | Report output directory |
| `JOBS_DB` | - | `jobs.db` | SQLite database path (ignored when `DATABASE_URL` is set) |
| `DATABASE_URL` | - | - | PostgreSQL URL; empty falls back to SQLite |
| `RATE_LIMIT_MAX_REQUESTS` | - | `10` | Submits allowed per window, per caller |
| `RATE_LIMIT_WINDOW` | - | `60` | Rate limit window, seconds |
| `TRUST_PROXY_HEADERS` | - | `true` | Identify callers via `X-Real-IP`/`X-Forwarded-For` |
| `CORS_ORIGINS` | - | `localhost:5173,4173` | Comma-separated allowed origins |
| `SCRAPER_BLOCK_PRIVATE_NETWORKS` | - | `true` | Refuse to fetch private/loopback/CGNAT addresses |
| `SCRAPER_MAX_REDIRECTS` | - | `5` | Redirect hops; each is re-validated |

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
uv run python -m evals.run --stages profile    # fast
uv run python -m evals.run --check             # fail on regression
```

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
# All tests
pytest tests/ -v

# Coverage
pytest tests/ --cov=app -v

# E2E smoke (requires NVIDIA_API_KEY)
pytest tests/smoke_test.py -v -k "e2e" --run-e2e
```

## Quality

```bash
ruff check .
mypy app
./scripts/check-design-rules.sh   # no gradients, small radii, tokens not hex
```
