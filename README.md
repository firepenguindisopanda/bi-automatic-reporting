# Business Intelligence System

Automated BI platform: scrapes websites, runs AI analysis (market, competitive, SWOT, marketing), generates PDF + DOCX reports, and emails them.

## Architecture

```
POST /api/bi/submit        → Submit URL + email → returns job_id
GET  /api/bi/status/{id}   → Poll job status + events
GET  /api/bi/download/{id} → Download report (?format=pdf|docx)
GET  /api/health           → Health check
```

Pipeline: **Scrape** → **Business Profile** → **Market Analysis** → **Competitive Analysis** → **SWOT** → **BI Report** → **Marketing Analysis** → **PDF/DOCX** → **Email**

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
| `NVIDIA_API_KEY` | ✅ | — | NVIDIA NIM API key |
| `NVIDIA_MODEL` | — | `meta/llama-3.1-8b-instruct` | LLM model ID |
| `SENDGRID_API_KEY` | — | — | SendGrid API key for email |
| `FROM_EMAIL` | — | `reports@bisystem.com` | Sender email |
| `REPORT_OUTPUT_DIR` | — | `reports` | Report output directory |
| `JOBS_DB` | — | `jobs.db` | SQLite database path |

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
```
