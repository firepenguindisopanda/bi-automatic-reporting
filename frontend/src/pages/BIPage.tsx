import { useCallback, useEffect, useRef, useState } from 'react'
import type { BIJobStatus } from '../types/api'

interface FormData {
  url: string
  email: string
}

type PageState =
  | { phase: 'form' }
  | { phase: 'submitting' }
  | { phase: 'polling'; jobId: string }
  | { phase: 'complete'; jobId: string }
  | { phase: 'error'; message: string; jobId?: string }

const STAGE_LABELS: Record<string, string> = {
  Scraper: 'Scraping website',
  'Business Profile': 'Analyzing business profile',
  'Market Analysis': 'Analyzing market',
  'Competitive Analysis': 'Analyzing competition',
  'SWOT Analysis': 'Generating SWOT',
  'Marketing Analysis': 'Marketing analysis',
  'Report Generation': 'Generating report',
  'Email Delivery': 'Sending email',
}

export default function BIPage() {
  const [form, setForm] = useState<FormData>({ url: '', email: '' })
  const [state, setState] = useState<PageState>({ phase: 'form' })
  const [events, setEvents] = useState<{ label: string; done: boolean }[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const valid = form.url.length >= 5 && form.email.length >= 5 && form.email.includes('@')

  const submit = useCallback(async () => {
    setState({ phase: 'submitting' })
    try {
      const resp = await fetch('/api/bi/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => null)
        setState({ phase: 'error', message: body?.detail?.[0]?.msg || `HTTP ${resp.status}` })
        return
      }
      const data = await resp.json()
      setState({ phase: 'polling', jobId: data.job_id })
    } catch {
      setState({ phase: 'error', message: 'Network error' })
    }
  }, [form])

  useEffect(() => {
    if (state.phase !== 'polling') return
    pollRef.current = setInterval(async () => {
      try {
        const resp = await fetch(`/api/bi/status/${state.jobId}`)
        if (!resp.ok) {
          setState({ phase: 'error', message: `Status check failed`, jobId: state.jobId })
          return
        }
        const data: BIJobStatus = await resp.json()
        if (data.progress) {
          setEvents(
            data.progress.map((e, i, arr) => ({
              label: STAGE_LABELS[e.agent] || e.agent,
              done: i < arr.length - 1 || data.status === 'complete',
            }))
          )
        }
        if (data.status === 'complete') {
          setState({ phase: 'complete', jobId: state.jobId })
        } else if (data.status === 'error') {
          setState({ phase: 'error', message: data.error || 'Unknown error', jobId: state.jobId })
        }
      } catch {
        setState({ phase: 'error', message: 'Network error during polling', jobId: state.jobId })
      }
    }, 3000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [state])

  const reset = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    setEvents([])
    setState({ phase: 'form' })
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Business Intelligence Analysis</h1>
      <p className="text-gray-600">
        Submit a website URL for automated business intelligence analysis.
      </p>

      {(state.phase === 'form' || state.phase === 'error') && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className="space-y-4"
        >
          <div>
            <label className="block text-sm font-medium text-gray-700">Website URL</label>
            <input
              type="url"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="https://example.com"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              required
              minLength={5}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Email</label>
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="you@example.com"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              required
              minLength={5}
            />
          </div>
          {state.phase === 'error' && (
            <p className="text-sm text-red-600">{state.message}</p>
          )}
          <button
            type="submit"
            disabled={!valid}
            className="rounded-md bg-indigo-700 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Start Analysis
          </button>
        </form>
      )}

      {state.phase === 'submitting' && (
        <div className="flex items-center gap-3 text-gray-600">
          <Spinner />
          Submitting...
        </div>
      )}

      {(state.phase === 'polling' || state.phase === 'complete') && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-gray-800">Progress</h2>
          <ul className="space-y-2">
            {events.length === 0 && (
              <li className="flex items-center gap-2 text-sm text-gray-500">
                <Spinner /> Waiting for pipeline to start...
              </li>
            )}
            {events.map((e, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                {e.done ? (
                  <span className="text-green-600">&#10003;</span>
                ) : (
                  <Spinner />
                )}
                <span className={e.done ? 'text-gray-600' : 'font-medium text-gray-900'}>
                  {e.label}
                </span>
              </li>
            ))}
          </ul>

          {state.phase === 'complete' && (
            <div className="space-y-3 pt-4">
              <p className="text-sm font-medium text-green-700">Analysis complete!</p>
              <div className="flex gap-3">
                <a
                  href={`/api/bi/download/${state.jobId}?format=pdf`}
                  className="rounded-md bg-indigo-700 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-800"
                >
                  Download PDF
                </a>
                <a
                  href={`/api/bi/download/${state.jobId}?format=docx`}
                  className="rounded-md border border-indigo-700 px-4 py-2 text-sm font-semibold text-indigo-700 shadow-sm hover:bg-indigo-50"
                >
                  Download DOCX
                </a>
              </div>
              <button
                onClick={reset}
                className="text-sm text-indigo-600 underline hover:text-indigo-800"
              >
                Analyze another site
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin text-indigo-600" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  )
}
