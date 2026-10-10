import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { JobHeader } from '../components/JobHeader'
import { StageTimeline } from '../components/StageTimeline'
import { Button, Callout, Card, LinkButton } from '../components/ui'
import { useJobStream } from '../hooks/useJobStream'
import { useRecentJobs } from '../hooks/useRecentJobs'
import { ApiError, api } from '../lib/api'
import { elapsed, hostname } from '../lib/format'
import type { JobProgress } from '../types/api'

/**
 * One job, live. The route carries the job id, so a refresh, a bookmark or a
 * link from another device all restore the same view - previously closing the
 * tab lost the job entirely.
 */
export default function JobPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [params] = useSearchParams()
  const kind = params.get('kind') === 'market_research' ? 'market_research' : 'bi'

  const { progress, connection, error, refresh, restart } = useJobStream(kind, jobId)
  const { jobs } = useRecentJobs()
  const remembered = jobs.find((j) => j.jobId === jobId)

  if (error && !progress) {
    return (
      <div className="space-y-6">
        <Callout tone="bad" title="Job not available">
          {error}
        </Callout>
        <Link
          to="/"
          className="inline-flex min-h-6 items-center font-sans text-[14px] text-accent"
        >
          Start a new analysis
        </Link>
      </div>
    )
  }

  if (!progress) {
    return <p className="text-muted">Loading…</p>
  }

  // The server names the job, so a link opened anywhere shows the site rather
  // than a bare id. Local history is only a fallback for jobs recorded before
  // the field existed.
  const subject = progress.subject
    ? kind === 'bi'
      ? hostname(progress.subject)
      : progress.subject
    : (remembered?.subject ?? jobId ?? 'Job')

  return (
    <div className="space-y-7">
      <JobHeader subject={subject} progress={progress} connection={connection} />

      <StageTimeline stages={progress.stages} />

      <Outcome progress={progress} kind={kind} onChanged={refresh} onRequeued={restart} />
    </div>
  )
}

function Outcome({
  progress,
  kind,
  onChanged,
  onRequeued,
}: {
  progress: JobProgress
  kind: 'bi' | 'market_research'
  onChanged: () => Promise<void>
  onRequeued: () => void
}) {
  if (progress.status === 'completed') {
    const research = kind === 'market_research'
    return (
      <Card className="p-5">
        <p className="text-[18px]">{research ? 'Research ready' : 'Report ready'}</p>
        <p className="mt-1 text-[15px] text-muted">
          Finished in {elapsed(progress.elapsed_ms)}
          {research ? '.' : ' and sent to your inbox.'}
        </p>
        {research ? (
          <p className="mt-3">
            <Link
              to={`/research/${progress.job_id}/result`}
              className="inline-flex min-h-6 items-center font-sans text-[15px] text-accent underline underline-offset-4"
            >
              Read the findings
            </Link>
          </p>
        ) : null}
        <Downloads kind={kind} jobId={progress.job_id} />
        <StartAnother />
      </Card>
    )
  }

  if (progress.status === 'partial' && kind === 'market_research') {
    // For research, `partial` means the analysis finished and the documents
    // did not render - there is nothing to download, but everything to read.
    return (
      <Card className="p-5">
        <p className="text-[18px]">Research ready, but the documents didn&rsquo;t render</p>
        <p className="mt-1 text-[15px] text-muted">
          {progress.error ?? 'The PDF and Word files could not be produced.'} The findings
          themselves are complete.
        </p>
        <p className="mt-3">
          <Link
            to={`/research/${progress.job_id}/result`}
            className="inline-flex min-h-6 items-center font-sans text-[15px] text-accent underline underline-offset-4"
          >
            Read the findings
          </Link>
        </p>
        <StartAnother />
      </Card>
    )
  }

  if (progress.status === 'partial') {
    return (
      <Card className="p-5">
        <p className="text-[18px]">Report ready, but the email didn&rsquo;t send</p>
        <p className="mt-1 text-[15px] text-muted">
          {progress.error ?? 'The mail provider rejected the message.'} The report itself is
          finished. Download it here, or try sending again.
        </p>
        <Downloads kind={kind} jobId={progress.job_id} />
        {kind === 'bi' ? <ResendButton jobId={progress.job_id} onDone={onChanged} /> : null}
        <StartAnother />
      </Card>
    )
  }

  if (progress.status === 'failed' || progress.status === 'interrupted') {
    const failed = progress.stages.find((s) => s.status === 'failed')
    return (
      <Card className="p-5">
        <p className="text-[18px] text-negative">
          {progress.status === 'interrupted' ? 'Interrupted' : 'Analysis stopped'}
        </p>
        <p className="mt-1 text-[15px] text-muted">
          {failed
            ? `It stopped at “${failed.label}”. ${failed.error ?? ''}`
            : (progress.error ?? 'Something went wrong.')}
        </p>
        {failed?.error_kind === 'blocked' ? (
          <p className="mt-2 text-[15px] text-muted">
            That address points inside a private network, so it wasn&rsquo;t fetched.
          </p>
        ) : (
          <RetryButton kind={kind} jobId={progress.job_id} onRequeued={onRequeued} />
        )}
        <StartAnother />
      </Card>
    )
  }

  return null
}

function Downloads({ kind, jobId }: { kind: 'bi' | 'market_research'; jobId: string }) {
  return (
    <div className="mt-4 flex flex-wrap gap-3">
      <LinkButton href={api.downloadUrl(kind, jobId, 'pdf')}>Download PDF</LinkButton>
      <LinkButton href={api.downloadUrl(kind, jobId, 'docx')} variant="secondary">
        Download Word
      </LinkButton>
    </div>
  )
}

function ResendButton({ jobId, onDone }: { jobId: string; onDone: () => Promise<void> }) {
  const [state, setState] = useState<'idle' | 'sending' | 'sent' | 'failed'>('idle')
  const [detail, setDetail] = useState('')

  const resend = async () => {
    setState('sending')
    try {
      const result = await api.resend(jobId)
      setState(result.sent ? 'sent' : 'failed')
      setDetail(result.detail)
      await onDone()
    } catch (e) {
      setState('failed')
      setDetail(e instanceof ApiError ? e.message : 'Could not reach the server.')
    }
  }

  if (state === 'sent') {
    return <p className="mt-4 font-sans text-[14px] text-positive">Sent.</p>
  }

  return (
    <div className="mt-4">
      <Button variant="secondary" onClick={resend} disabled={state === 'sending'}>
        {state === 'sending' ? 'Sending…' : 'Try sending again'}
      </Button>
      {state === 'failed' ? (
        <p className="mt-2 font-sans text-[14px] text-negative">{detail}</p>
      ) : null}
    </div>
  )
}

/**
 * Resume from the first stage that did not finish. Stages that succeeded keep
 * their stored output, so a retry costs only what is left.
 */
function RetryButton({
  kind,
  jobId,
  onRequeued,
}: {
  kind: 'bi' | 'market_research'
  jobId: string
  onRequeued: () => void
}) {
  const [state, setState] = useState<'idle' | 'sending' | 'failed'>('idle')
  const [detail, setDetail] = useState('')

  const retry = async () => {
    setState('sending')
    try {
      await api.retry(kind, jobId)
      onRequeued()
    } catch (e) {
      setState('failed')
      setDetail(e instanceof ApiError ? e.message : 'Could not reach the server.')
    }
  }

  return (
    <div className="mt-4">
      <Button variant="secondary" onClick={retry} disabled={state === 'sending'}>
        {state === 'sending' ? 'Resuming…' : 'Resume from where it stopped'}
      </Button>
      {state === 'failed' ? (
        <p role="alert" className="mt-2 font-sans text-[14px] text-negative">
          {detail}
        </p>
      ) : null}
    </div>
  )
}

function StartAnother() {
  return (
    <p className="mt-4">
      <Link
        to="/"
        className="inline-flex min-h-6 items-center font-sans text-[14px] text-accent
                   underline underline-offset-4"
      >
        Start another
      </Link>
    </p>
  )
}
