import { useState } from 'react'
import { duration, modelName, tokens } from '../lib/format'
import type { StageReport } from '../types/api'
import { Leader, QualityMark, Spinner } from './ui'

/**
 * The pipeline rendered as a report's table of contents: each stage is a line,
 * leader dots carry the eye across, and a fixed-width folio on the right holds
 * what was measured. The running stage is marked with a rule in the margin, the
 * way an editor marks the line they are working on.
 *
 * Stages come from the API, so this never needs to know the pipeline's shape.
 */
export function StageTimeline({ stages }: { stages: StageReport[] }) {
  return (
    <ol className="border-t border-rule">
      {stages.map((stage) => (
        <StageRow key={stage.stage} stage={stage} />
      ))}
    </ol>
  )
}

function StageRow({ stage }: { stage: StageReport }) {
  const [open, setOpen] = useState(false)

  const running = stage.status === 'running'
  const failed = stage.status === 'failed'
  const done = stage.status === 'succeeded'
  const pending = stage.status === 'pending'
  const skipped = stage.status === 'skipped'

  // Nothing to reveal for a stage that has not run yet.
  const expandable =
    !pending &&
    (stage.warnings.length > 0 ||
      Boolean(stage.critic_note) ||
      Boolean(stage.error) ||
      Boolean(stage.model) ||
      stage.attempts > 1)

  const row = (
    <div className="flex w-full min-w-0 items-baseline gap-2.5 px-3 py-2.5 text-left sm:gap-3 sm:px-4">
      <StatusGlyph status={stage.status} />

      <span
        className={
          'min-w-0 flex-1 text-[15px] sm:flex-none sm:text-[16px] ' +
          (failed ? 'text-negative' : pending ? 'text-faint' : 'text-ink')
        }
      >
        {stage.label}
        {skipped ? <span className="text-muted"> (skipped)</span> : null}
      </span>

      {/* The leader needs room to read as a leader; below `sm` there isn't any. */}
      <span className="hidden sm:contents">
        <Leader />
      </span>

      {/* Fixed-width folio. A rigid grid, not flexible children: the durations
          must land on the same x down the whole page, like page numbers.
          The attempts column folds away on narrow screens - it is also in the
          expanded detail, so nothing is lost. */}
      <span
        className="grid shrink-0 grid-cols-[1.25rem_3.25rem_0.75rem] items-center gap-1.5
                   sm:grid-cols-[1.25rem_1.5rem_3.25rem_0.75rem]"
      >
        <span className="flex justify-end">
          {stage.completeness !== null && done ? (
            <QualityMark score={stage.completeness} label="Completeness" />
          ) : null}
        </span>
        <span className="numeric hidden text-right text-muted sm:block">
          {stage.attempts > 1 ? `×${stage.attempts}` : ''}
        </span>
        <span className="numeric text-right text-muted">
          {running ? '···' : duration(stage.duration_ms)}
        </span>
        <span
          aria-hidden="true"
          className={
            'text-center font-sans text-[10px] text-faint ' + (expandable ? '' : 'invisible')
          }
        >
          {open ? '▾' : '▸'}
        </span>
      </span>
    </div>
  )

  return (
    <li
      className={
        // The marginal rule: only the running stage carries the accent edge.
        'border-b border-rule border-l-2 ' +
        (running ? 'border-l-accent bg-accent-soft/40' : 'border-l-transparent')
      }
    >
      {expandable ? (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="block w-full hover:bg-sunk"
        >
          {row}
        </button>
      ) : (
        row
      )}

      {open ? (
        <dl className="mb-3 ml-6 mr-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 border-l border-rule pl-3 sm:ml-8 sm:mr-4 sm:gap-x-4">
          {stage.model ? (
            <Detail term="Model">
              <span className="numeric">{modelName(stage.model)}</span>
            </Detail>
          ) : null}
          {stage.attempts > 1 ? (
            <Detail term="Attempts">
              <span className="numeric">{stage.attempts}</span>
              <span className="ml-2 text-muted">
                the first reply could not be used, so it was asked again
              </span>
            </Detail>
          ) : null}
          {stage.prompt_tokens ? (
            <Detail term="Tokens">
              <span className="numeric">
                {tokens(stage.prompt_tokens)} in · {tokens(stage.completion_tokens)} out
              </span>
            </Detail>
          ) : null}
          {stage.completeness !== null ? (
            <Detail term="Completeness">
              <span className="numeric">{Math.round(stage.completeness)}/100</span>
            </Detail>
          ) : null}
          {stage.confidence !== null ? (
            <Detail term="Reviewer">
              <span className="numeric">{Math.round(stage.confidence)}/100</span>
              {stage.critic_note ? (
                <span className="ml-2 text-muted">{stage.critic_note}</span>
              ) : null}
            </Detail>
          ) : null}
          {stage.warnings.length > 0 ? (
            <Detail term="Notes">
              <ul className="space-y-1">
                {stage.warnings.map((w, i) => (
                  <li key={i} className="text-muted">
                    {w}
                  </li>
                ))}
              </ul>
            </Detail>
          ) : null}
          {stage.error ? (
            <Detail term="Error">
              <span className="text-negative">{stage.error}</span>
              {stage.error_kind ? (
                <span className="numeric ml-2 text-muted">{stage.error_kind}</span>
              ) : null}
            </Detail>
          ) : null}
        </dl>
      ) : null}
    </li>
  )
}

function Detail({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="eyebrow pt-1">{term}</dt>
      <dd className="text-[14px]">{children}</dd>
    </>
  )
}

function StatusGlyph({ status }: { status: StageReport['status'] }) {
  const glyph = {
    running: null,
    succeeded: { mark: '✓', tone: 'text-positive' },
    failed: { mark: '✕', tone: 'text-negative' },
    skipped: { mark: '–', tone: 'text-faint' },
    pending: { mark: '·', tone: 'text-faint' },
  }[status]

  if (glyph === null) return <Spinner label="In progress" />
  return (
    <span aria-hidden="true" className={`w-3 shrink-0 text-[13px] ${glyph.tone}`}>
      {glyph.mark}
    </span>
  )
}
