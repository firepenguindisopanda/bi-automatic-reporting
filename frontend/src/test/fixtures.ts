import type { JobProgress, StageReport } from '../types/api'

/** A stage that has not started. Override only what a test is about. */
export function makeStage(overrides: Partial<StageReport> = {}): StageReport {
  return {
    stage: 'scrape',
    display: 'Scrape',
    label: 'Reading the website',
    seq: 1,
    status: 'pending',
    duration_ms: null,
    model: null,
    attempts: 1,
    prompt_tokens: null,
    completion_tokens: null,
    completeness: null,
    confidence: null,
    critic_note: null,
    warnings: [],
    error: null,
    error_kind: null,
    ...overrides,
  }
}

export function makeProgress(overrides: Partial<JobProgress> = {}): JobProgress {
  return {
    job_id: 'job-123',
    kind: 'bi',
    status: 'running',
    error: null,
    subject: 'https://www.acme.com',
    stages: [makeStage()],
    elapsed_ms: 0,
    eta_ms: null,
    quality: null,
    confidence: null,
    queue_position: null,
    ...overrides,
  }
}

/** A minimal fetch Response: `request()` only reads ok, status and json(). */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
