import { elapsed, eta } from '../lib/format'
import type { Connection } from '../hooks/useJobStream'
import { isTerminal, type JobProgress } from '../types/api'
import { QualityMark } from './ui'

/**
 * The masthead for a running job: what is being analysed, how long it has
 * taken, what is left, and how good the output is so far.
 *
 * The active stage is announced politely so a screen reader follows along
 * without the whole timeline being re-read on every tick.
 */
export function JobHeader({
  subject,
  progress,
  connection,
}: {
  subject: string
  progress: JobProgress
  connection: Connection
}) {
  const active = progress.stages.find((s) => s.status === 'running')
  // Nothing is left to wait for once the job has settled, whatever the server
  // sent - a finished job showing "about a minute left" reads as still running.
  const remaining = isTerminal(progress.status) ? null : eta(progress.eta_ms)
  const queued =
    !isTerminal(progress.status) &&
    progress.queue_position !== null &&
    progress.queue_position > 0

  return (
    <header className="border-b border-rule pb-4">
      <p className="eyebrow">
        {progress.kind === 'bi' ? 'Business intelligence' : 'Market research'}
      </p>

      <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h1 className="text-[26px] leading-tight sm:text-[30px]">{subject}</h1>
        <span className="numeric text-muted">{elapsed(progress.elapsed_ms)}</span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 font-sans text-[13px] text-muted">
        {queued ? (
          <span>
            Queued — {progress.queue_position} ahead of this one
          </span>
        ) : remaining ? (
          <span>{remaining}</span>
        ) : null}

        {progress.quality !== null ? (
          <span className="inline-flex items-center gap-2">
            <QualityMark score={progress.quality} label="Overall quality" />
            Quality {Math.round(progress.quality)}
            {/* The reviewer's score belongs next to completeness, not instead
                of it: a structurally complete answer can still be generic. */}
            {progress.confidence !== null ? (
              <span className="text-faint">
                · reviewer {Math.round(progress.confidence)}
              </span>
            ) : null}
          </span>
        ) : null}

        {connection === 'polling' ? (
          <span className="text-warning">Live updates unavailable — checking every 3s</span>
        ) : null}
      </div>

      <p aria-live="polite" className="sr-only">
        {active ? `${active.label}. ${remaining ?? ''}` : `Job ${progress.status}.`}
      </p>
    </header>
  )
}
