import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../lib/api'
import { FakeEventSource, installFakeEventSource } from '../test/fakeEventSource'
import { makeProgress, makeStage } from '../test/fixtures'
import type { JobProgress } from '../types/api'
import JobPage from './JobPage'

beforeEach(() => {
  installFakeEventSource()
})

function renderJob(progress: JobProgress | Error, path = '/jobs/job-123') {
  const progressSpy = vi.spyOn(api, 'progress')
  if (progress instanceof Error) progressSpy.mockRejectedValue(progress)
  else progressSpy.mockResolvedValue(progress)

  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/jobs/:jobId" element={<JobPage />} />
      </Routes>
    </MemoryRouter>,
  )
  return progressSpy
}

function link(name: string | RegExp): HTMLAnchorElement {
  const element = screen.getByRole('link', { name })
  if (!(element instanceof HTMLAnchorElement)) throw new Error('Expected an anchor')
  return element
}

const finishedStages = [
  makeStage({ stage: 'scrape', label: 'Reading the website', status: 'succeeded' }),
  makeStage({ stage: 'write', label: 'Writing the report', seq: 2, status: 'succeeded' }),
]

describe('JobPage', () => {
  it('names the job by the site analysed and lists its stages', async () => {
    renderJob(makeProgress({ subject: 'https://www.acme.com', stages: finishedStages }))

    expect(await screen.findByRole('heading', { name: 'acme.com' })).toBeTruthy()
    expect(screen.getByText('Reading the website')).toBeTruthy()
    expect(screen.getByText('Writing the report')).toBeTruthy()
  })

  it('offers both downloads once the report is complete', async () => {
    renderJob(makeProgress({ status: 'completed', elapsed_ms: 125_000, stages: finishedStages }))

    expect(await screen.findByText('Report ready')).toBeTruthy()
    expect(screen.getByText(/Finished in 2m 5s and sent to your inbox/)).toBeTruthy()
    expect(link('Download PDF').getAttribute('href')).toBe('/api/bi/download/job-123?format=pdf')
    expect(link('Download Word').getAttribute('href')).toBe(
      '/api/bi/download/job-123?format=docx',
    )
    expect(screen.queryByRole('button', { name: 'Try sending again' })).toBeNull()
  })

  it('links completed research to its findings and research downloads', async () => {
    renderJob(
      makeProgress({ kind: 'market_research', status: 'completed', subject: 'EV charging' }),
      '/jobs/job-123?kind=market_research',
    )

    expect(await screen.findByText('Research ready')).toBeTruthy()
    expect(link('Read the findings').getAttribute('href')).toBe('/research/job-123/result')
    expect(link('Download PDF').getAttribute('href')).toBe(
      '/api/market-research/download/job-123?format=pdf',
    )
  })

  it('on a partial result, offers downloads and a resend that reports success', async () => {
    const user = userEvent.setup()
    const progressSpy = renderJob(
      makeProgress({ status: 'partial', error: 'Mailbox unavailable.', stages: finishedStages }),
    )
    const resendSpy = vi
      .spyOn(api, 'resend')
      .mockResolvedValue({ job_id: 'job-123', sent: true, detail: 'Sent' })

    expect(await screen.findByText('Report ready, but the email didn’t send')).toBeTruthy()
    expect(screen.getByText(/Mailbox unavailable\./)).toBeTruthy()
    expect(link('Download PDF')).toBeTruthy()
    const callsBefore = progressSpy.mock.calls.length

    await user.click(screen.getByRole('button', { name: 'Try sending again' }))

    expect(resendSpy).toHaveBeenCalledWith('job-123')
    expect(await screen.findByText('Sent.')).toBeTruthy()
    // The page re-reads the job so the status reflects the new send.
    expect(progressSpy.mock.calls.length).toBe(callsBefore + 1)
  })

  it('on a partial result, explains a resend that fails', async () => {
    const user = userEvent.setup()
    renderJob(makeProgress({ status: 'partial', stages: finishedStages }))
    vi.spyOn(api, 'resend').mockRejectedValue(new ApiError('Too many requests.', 429))

    await user.click(await screen.findByRole('button', { name: 'Try sending again' }))

    expect(await screen.findByText('Too many requests.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try sending again' })).toBeTruthy()
  })

  it('on failure, names the stage that stopped and its error, without downloads', async () => {
    renderJob(
      makeProgress({
        status: 'failed',
        stages: [
          makeStage({ label: 'Reading the website', status: 'succeeded' }),
          makeStage({
            stage: 'market',
            label: 'Sizing the market',
            seq: 2,
            status: 'failed',
            error: 'The model timed out.',
          }),
        ],
      }),
    )

    expect(await screen.findByText('Analysis stopped')).toBeTruthy()
    expect(screen.getByText('It stopped at “Sizing the market”. The model timed out.')).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Download PDF' })).toBeNull()
    expect(link('Start another').getAttribute('href')).toBe('/')
  })

  it('explains a blocked address and falls back to the job error', async () => {
    renderJob(
      makeProgress({
        status: 'failed',
        stages: [makeStage({ status: 'failed', error: 'Refused.', error_kind: 'blocked' })],
      }),
    )
    expect(await screen.findByText(/points inside a private network/)).toBeTruthy()
  })

  it('shows the job error when an interrupted job has no failed stage', async () => {
    renderJob(makeProgress({ status: 'interrupted', error: 'The server restarted.' }))

    expect(await screen.findByText('Interrupted')).toBeTruthy()
    expect(screen.getByText('The server restarted.')).toBeTruthy()
  })

  it('says so when the job does not exist', async () => {
    renderJob(new ApiError('Not found.', 404))

    expect(await screen.findByText('Job not available')).toBeTruthy()
    expect(screen.getByText('This job no longer exists.')).toBeTruthy()
    expect(link('Start a new analysis').getAttribute('href')).toBe('/')
  })

  it('offers to resume a failed job, and reconnects once it is queued again', async () => {
    const user = userEvent.setup()
    renderJob(
      makeProgress({
        status: 'failed',
        stages: [makeStage({ status: 'failed', error: 'The model timed out.' })],
      }),
    )
    const retrySpy = vi
      .spyOn(api, 'retry')
      .mockResolvedValue({ job_id: 'job-123', status: 'queued' })
    const streamsBefore = FakeEventSource.instances.length

    await user.click(await screen.findByRole('button', { name: 'Resume from where it stopped' }))

    expect(retrySpy).toHaveBeenCalledWith('bi', 'job-123')
    // The finished job's stream had closed; a resumed one needs a new stream.
    expect(FakeEventSource.instances.length).toBe(streamsBefore + 1)
  })

  it('explains a resume the server refuses', async () => {
    const user = userEvent.setup()
    renderJob(makeProgress({ status: 'interrupted', error: 'The server restarted.' }))
    vi.spyOn(api, 'retry').mockRejectedValue(
      new ApiError('This job has already been retried 2 times', 429),
    )

    await user.click(await screen.findByRole('button', { name: 'Resume from where it stopped' }))

    expect((await screen.findByRole('alert')).textContent).toBe(
      'This job has already been retried 2 times',
    )
  })

  it('does not offer to resume a blocked address', async () => {
    renderJob(
      makeProgress({
        status: 'failed',
        stages: [makeStage({ status: 'failed', error: 'Refused.', error_kind: 'blocked' })],
      }),
    )
    expect(await screen.findByText(/points inside a private network/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Resume from where it stopped' })).toBeNull()
  })

  it('on partial research, links the findings and offers no downloads', async () => {
    renderJob(
      makeProgress({
        kind: 'market_research',
        status: 'partial',
        error: 'The analysis is complete but the documents could not be rendered: pango missing',
      }),
      '/jobs/job-123?kind=market_research',
    )

    expect(await screen.findByText('Research ready, but the documents didn’t render')).toBeTruthy()
    expect(link('Read the findings').getAttribute('href')).toBe('/research/job-123/result')
    expect(screen.queryByRole('link', { name: 'Download PDF' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try sending again' })).toBeNull()
  })
})
