import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useRecentJobs } from '../hooks/useRecentJobs'
import { ApiError, api, looksLikeEmail, looksLikeUrl, normalizeUrl } from '../lib/api'
import { hostname } from '../lib/format'
import type { AgentModels } from '../types/api'
import { Button, Callout, Card, Field } from '../components/ui'

export default function BIPage() {
  const navigate = useNavigate()
  const { jobs, remember, forget } = useRecentJobs()

  const [url, setUrl] = useState('')
  const [email, setEmail] = useState('')
  const [touched, setTouched] = useState({ url: false, email: false })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const urlError = touched.url && url && !looksLikeUrl(url) ? 'Enter a full website address.' : null
  const emailError =
    touched.email && email && !looksLikeEmail(email) ? 'Enter a valid email address.' : null
  const ready = looksLikeUrl(url) && looksLikeEmail(email)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setTouched({ url: true, email: true })
    if (!ready || submitting) return

    setSubmitting(true)
    setError(null)
    const target = normalizeUrl(url)
    try {
      const job = await api.submitAnalysis(target, email.trim())
      remember({
        jobId: job.job_id,
        kind: 'bi',
        subject: hostname(target),
        startedAt: Date.now(),
      })
      navigate(`/jobs/${job.job_id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the analysis.')
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-10">
      <section>
        <p className="eyebrow">Analysis</p>
        <h1 className="mt-1 text-[32px] leading-tight sm:text-[38px]">
          Turn a website into a strategic report
        </h1>
        <p className="mt-3 max-w-[52ch] text-[17px] text-muted">
          Give a company&rsquo;s address and we&rsquo;ll read the site, work out what the business
          does, size its market, map its competitors, and write the report. It takes a few minutes;
          you can close the page and come back.
        </p>
      </section>

      <Card className="p-5 sm:p-6">
        <form onSubmit={submit} noValidate className="space-y-5">
          <Field
            label="Website"
            type="text"
            inputMode="url"
            autoComplete="url"
            placeholder="acme.com"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, url: true }))}
            error={urlError}
            hint={!urlError && url && looksLikeUrl(url) ? normalizeUrl(url) : undefined}
          />

          <Field
            label="Where to send it"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setTouched((t) => ({ ...t, email: true }))}
            error={emailError}
            hint="The report is also downloadable here when it's done."
          />

          {error ? (
            <Callout tone="bad" title="Could not start">
              {error}
            </Callout>
          ) : null}

          <Button type="submit" disabled={!ready || submitting}>
            {submitting ? 'Starting…' : 'Start analysis'}
          </Button>
        </form>
      </Card>

      {jobs.length > 0 ? (
        <section>
          <h2 className="eyebrow">Recent</h2>
          <ul className="mt-3 border-t border-rule">
            {jobs.map((job) => (
              <li key={job.jobId} className="flex items-baseline gap-3 border-b border-rule py-2.5">
                <Link
                  to={`/jobs/${job.jobId}${job.kind === 'market_research' ? '?kind=market_research' : ''}`}
                  className="inline-flex min-h-6 items-center text-[16px] no-underline hover:text-accent"
                >
                  {job.subject}
                </Link>
                <span className="numeric text-faint">
                  {job.kind === 'bi' ? 'analysis' : 'research'}
                </span>
                <span aria-hidden="true" className="leader" />
                <button
                  type="button"
                  onClick={() => forget(job.jobId)}
                  className="inline-flex min-h-6 items-center font-sans text-[12px] text-faint underline underline-offset-4 hover:text-negative"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <ModelConfiguration />
    </div>
  )
}

/** Which model runs which stage. Collapsed: useful, not the point of the page. */
function ModelConfiguration() {
  const [open, setOpen] = useState(false)
  const [models, setModels] = useState<AgentModels | null>(null)

  useEffect(() => {
    if (!open || models) return
    api
      .models()
      .then(setModels)
      .catch(() => setModels({}))
  }, [open, models])

  return (
    <section className="border-t border-rule pt-5">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="font-sans text-[13px] text-muted underline underline-offset-4 hover:text-accent"
      >
        {open ? 'Hide model configuration' : 'Model configuration'}
      </button>

      {open && models ? (
        <dl className="mt-3 space-y-1">
          {Object.entries(models).map(([stage, model]) => (
            <div key={stage} className="flex items-baseline gap-3">
              <dt className="shrink-0 text-[15px] text-muted">{stage}</dt>
              <span aria-hidden="true" className="leader" />
              <dd className="numeric shrink-0 text-muted">{model}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  )
}
