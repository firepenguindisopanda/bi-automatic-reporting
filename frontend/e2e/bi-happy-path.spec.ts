import { expect, test } from '@playwright/test'
import type { JobProgress, StageReport, StageStatus } from '../src/types/api.ts'

const JOB_ID = 'e2e-job-1'

const STAGES = [
  { stage: 'scrape', label: 'Reading the website', duration_ms: 4200, shown: '4.2s' },
  { stage: 'profile', label: 'Profiling the business', duration_ms: 18_300, shown: '18.3s' },
  { stage: 'market', label: 'Sizing the market', duration_ms: 31_000, shown: '31.0s' },
  { stage: 'report', label: 'Writing the report', duration_ms: 75_000, shown: '1m 15s' },
] as const

/** The job as the API would report it, with each stage at the given status. */
function snapshot(statuses: StageStatus[], status: JobProgress['status']): JobProgress {
  const stages: StageReport[] = STAGES.map((s, i) => {
    const stageStatus = statuses[i] ?? 'pending'
    const done = stageStatus === 'succeeded'
    return {
      stage: s.stage,
      display: s.stage,
      label: s.label,
      seq: i + 1,
      status: stageStatus,
      duration_ms: done ? s.duration_ms : null,
      model: 'meta/llama-3.1-8b-instruct',
      attempts: 1,
      prompt_tokens: done ? 2400 : null,
      completion_tokens: done ? 600 : null,
      completeness: done ? 90 : null,
      confidence: done ? 80 : null,
      critic_note: null,
      warnings: [],
      error: null,
      error_kind: null,
    }
  })
  return {
    job_id: JOB_ID,
    kind: 'bi',
    status,
    error: null,
    subject: 'https://acme.com',
    stages,
    elapsed_ms: 128_500,
    eta_ms: status === 'completed' ? null : 90_000,
    quality: status === 'completed' ? 91 : null,
    confidence: status === 'completed' ? 82 : null,
    queue_position: null,
  }
}

/** Server-sent events wire format: one `event:`/`data:` block per frame. */
function eventStream(frames: { event: string; data: unknown }[]): string {
  return frames.map((f) => `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`).join('')
}

test('submit a site, follow the job live, and reach the downloads', async ({ page }) => {
  // Anything not mocked below is a test failure, never a call to a real backend.
  const unmocked: string[] = []
  await page.route('**/api/**', async (route) => {
    unmocked.push(`${route.request().method()} ${route.request().url()}`)
    await route.fulfill({ status: 404, json: { detail: 'Not mocked' } })
  })

  let submitted: unknown = null
  await page.route('**/api/bi/submit', async (route) => {
    submitted = route.request().postDataJSON()
    await route.fulfill({ json: { job_id: JOB_ID, status: 'queued', message: 'Queued' } })
  })

  await page.route(`**/api/bi/progress/${JOB_ID}`, (route) =>
    route.fulfill({ json: snapshot(['running'], 'running') }),
  )

  // The stream is held until the test has seen the job running, so the run is
  // observed going from in progress to complete rather than only its end state.
  let releaseStream = () => {}
  const streamReleased = new Promise<void>((resolve) => {
    releaseStream = resolve
  })
  await page.route(
    (url) => url.pathname === `/api/jobs/${JOB_ID}/events`,
    async (route) => {
      await streamReleased
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body: eventStream([
          { event: 'progress', data: snapshot(['running'], 'running') },
          { event: 'progress', data: snapshot(['succeeded', 'running'], 'running') },
          { event: 'progress', data: snapshot(['succeeded', 'succeeded', 'running'], 'running') },
          {
            event: 'progress',
            data: snapshot(['succeeded', 'succeeded', 'succeeded', 'running'], 'running'),
          },
          {
            event: 'progress',
            data: snapshot(['succeeded', 'succeeded', 'succeeded', 'succeeded'], 'completed'),
          },
          { event: 'done', data: { job_id: JOB_ID } },
        ]),
      })
    },
  )

  await page.goto('/reports/')

  const start = page.getByRole('button', { name: 'Start analysis' })
  await expect(start).toBeDisabled()
  await page.getByLabel('Website').fill('acme.com')
  await page.getByLabel('Where to send it').fill('me@acme.com')
  await start.click()

  await expect(page).toHaveURL(new RegExp(`/reports/jobs/${JOB_ID}$`))
  expect(submitted).toEqual({ url: 'https://acme.com', email: 'me@acme.com' })
  await expect(page.getByRole('heading', { level: 1, name: 'acme.com' })).toBeVisible()

  // Before the stream delivers anything: the first stage is running.
  const firstRow = page.getByRole('listitem').filter({ hasText: STAGES[0].label })
  await expect(firstRow.getByRole('status', { name: 'In progress' })).toBeVisible()
  await expect(page.getByText('Report ready')).toHaveCount(0)

  releaseStream()

  for (const stage of STAGES) {
    const row = page.getByRole('listitem').filter({ hasText: stage.label })
    await expect(row).toContainText('✓')
    await expect(row).toContainText(stage.shown)
    await expect(row.getByLabel('Completeness 90 of 100')).toBeVisible()
  }
  await expect(page.getByRole('status', { name: 'In progress' })).toHaveCount(0)

  await expect(page.getByText('Report ready')).toBeVisible()
  await expect(page.getByText('Quality 91')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Download PDF' })).toHaveAttribute(
    'href',
    `/api/bi/download/${JOB_ID}?format=pdf`,
  )
  await expect(page.getByRole('link', { name: 'Download Word' })).toHaveAttribute(
    'href',
    `/api/bi/download/${JOB_ID}?format=docx`,
  )

  expect(unmocked).toEqual([])
})
