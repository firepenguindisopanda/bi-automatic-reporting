import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { useId } from 'react'

function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

/* ---------------------------------------------------------------------------
   Surfaces
   --------------------------------------------------------------------------- */

export function Card({
  children,
  className,
  tone = 'plain',
}: {
  children: ReactNode
  className?: string
  tone?: 'plain' | 'lifted'
}) {
  return (
    <div
      className={cx(
        'rounded-card border border-rule bg-surface',
        tone === 'lifted' ? 'shadow-lift' : 'shadow-card',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function Callout({
  tone,
  title,
  children,
}: {
  tone: 'note' | 'good' | 'warn' | 'bad'
  title: string
  children?: ReactNode
}) {
  const border = {
    note: 'border-rule-strong',
    good: 'border-positive',
    warn: 'border-warning',
    bad: 'border-negative',
  }[tone]
  const text = {
    note: 'text-ink',
    good: 'text-positive',
    warn: 'text-warning',
    bad: 'text-negative',
  }[tone]

  return (
    <div className={cx('rounded-card border border-l-2 border-rule bg-surface p-4', border)}>
      <p className={cx('font-sans text-sm font-semibold', text)}>{title}</p>
      {children ? <div className="mt-1.5 text-[15px] text-muted">{children}</div> : null}
    </div>
  )
}

/* ---------------------------------------------------------------------------
   Controls
   --------------------------------------------------------------------------- */

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'quiet'
}

const BUTTON_BASE =
  'inline-flex min-h-11 items-center justify-center gap-2 rounded-sm px-4 font-sans ' +
  'text-sm font-semibold transition-colors disabled:cursor-not-allowed'

// A disabled control goes neutral rather than a faded accent: a translucent
// accent reads as a rendering fault, and the text stops being legible.
const BUTTON_VARIANTS = {
  primary:
    'bg-accent text-paper hover:opacity-90 disabled:bg-sunk disabled:text-faint ' +
    'disabled:ring-1 disabled:ring-rule',
  secondary:
    'border border-rule-strong bg-surface text-ink hover:border-accent hover:text-accent ' +
    'disabled:text-faint disabled:border-rule',
  quiet: 'text-muted underline underline-offset-4 hover:text-accent disabled:text-faint',
} as const

export function Button({ variant = 'primary', className, ...props }: ButtonProps) {
  return <button className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className)} {...props} />
}

export function LinkButton({
  href,
  variant = 'primary',
  children,
  download,
}: {
  href: string
  variant?: 'primary' | 'secondary'
  children: ReactNode
  download?: boolean
}) {
  return (
    <a
      href={href}
      download={download}
      className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], 'no-underline')}
    >
      {children}
    </a>
  )
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  hint?: string
  error?: string | null
}

export function Field({ label, hint, error, className, ...props }: FieldProps) {
  const id = useId()
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined

  return (
    <div className={className}>
      <label htmlFor={id} className="eyebrow mb-1.5 block">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        className={cx(
          'block min-h-11 w-full rounded-sm border bg-surface px-3 py-2',
          'font-sans text-[15px] text-ink placeholder:text-faint',
          error ? 'border-negative' : 'border-rule-strong',
        )}
        {...props}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 font-sans text-[13px] text-negative">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="mt-1.5 font-sans text-[13px] text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

/* ---------------------------------------------------------------------------
   Status
   --------------------------------------------------------------------------- */

/** Marks the running stage. Static under reduced motion via the global rule. */
export function Spinner({ label = 'Working' }: { label?: string }) {
  return (
    <span
      role="status"
      aria-label={label}
      className="inline-block size-3 shrink-0 animate-pulse rounded-full bg-accent"
    />
  )
}

/**
 * Quality as a marginal mark rather than a bar: three discrete segments, in the
 * spirit of a proof mark in a manuscript margin. Discrete because the score is
 * a judgement with a coarse meaning, not a precise measurement.
 */
export function QualityMark({ score, label }: { score: number; label: string }) {
  const filled = score >= 85 ? 3 : score >= 60 ? 2 : score > 0 ? 1 : 0
  const tone =
    filled === 3 ? 'bg-positive' : filled === 2 ? 'bg-warning' : 'bg-negative'

  return (
    <span
      className="inline-flex items-center gap-0.5"
      title={`${label} ${Math.round(score)} of 100`}
      aria-label={`${label} ${Math.round(score)} of 100`}
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          aria-hidden="true"
          className={cx('h-2.5 w-1 rounded-[1px]', i < filled ? tone : 'bg-rule-strong')}
        />
      ))}
    </span>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-card border border-dashed border-rule-strong px-6 py-10 text-center">
      <p className="text-[17px] text-ink">{title}</p>
      {children ? <div className="mt-2 text-[15px] text-muted">{children}</div> : null}
    </div>
  )
}

/** A dotted leader tying a label to its value, as in a table of contents. */
export function Leader() {
  return <span aria-hidden="true" className="leader" />
}
