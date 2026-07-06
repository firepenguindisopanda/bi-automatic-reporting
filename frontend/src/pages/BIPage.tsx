import { useCallback, useEffect, useRef, useState } from 'react'
import type { AgentModels, BIJobStatus } from '../types/api'

interface FormData {
  url: string
  email: string
}

type PageState =
  | { phase: 'form' }
  | { phase: 'submitting' }
  | { phase: 'polling'; jobId: string; startTime: number }
  | { phase: 'complete'; jobId: string }
  | { phase: 'error'; message: string; jobId?: string }

const AGENT_ORDER = [
  'Scraper',
  'Business Profile',
  'Market Analysis',
  'Competitive Analysis',
  'SWOT Analysis',
  'Marketing Analysis',
  'Report Generation',
  'Email Delivery',
]

const STAGE_LABELS: Record<string, string> = {
  Scraper: 'Scraping website content',
  'Business Profile': 'Analyzing business profile',
  'Market Analysis': 'Analyzing market landscape',
  'Competitive Analysis': 'Analyzing competition',
  'SWOT Analysis': 'Generating SWOT analysis',
  'Marketing Analysis': 'Marketing analysis',
  'Report Generation': 'Generating report',
  'Email Delivery': 'Sending email',
}

function elapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const sec = s % 60
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

function agentStarted(events: { type: string; agent: string }[], agent: string): boolean {
  if (agent === 'Email Delivery') {
    return events.some(e => e.type === 'sending_email' && e.agent === agent)
  }
  return events.some(e => e.type === 'step_start' && e.agent === agent)
}

function agentCompleted(events: { type: string; agent: string }[], agent: string): 'yes' | 'no' | 'failed' {
  if (agent === 'Email Delivery') {
    if (events.some(e => e.type === 'email_sent' && e.agent === agent)) return 'yes'
    if (events.some(e => e.type === 'email_failed' && e.agent === agent)) return 'failed'
    return 'no'
  }
  return events.some(e => e.type === 'step_complete' && e.agent === agent) ? 'yes' : 'no'
}

export default function BIPage() {
  const [form, setForm] = useState<FormData>({ url: '', email: '' })
  const [state, setState] = useState<PageState>({ phase: 'form' })
  const [events, setEvents] = useState<{ type: string; agent: string }[]>([])
  const [elapsedMs, setElapsedMs] = useState(0)
  const [agentModels, setAgentModels] = useState<AgentModels | null>(null)
  const [showModels, setShowModels] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const valid = form.url.length >= 5 && form.email.length >= 5 && form.email.includes('@')

  useEffect(() => {
    fetch('/api/bi/models').then(r => r.json()).then(setAgentModels).catch(() => {})
  }, [])

  const currentAgentIdx = (() => {
    for (const agent of AGENT_ORDER) {
      if (agentStarted(events, agent) && agentCompleted(events, agent) === 'no') return AGENT_ORDER.indexOf(agent)
    }
    return -1
  })()

  const currentAgent = currentAgentIdx >= 0 ? AGENT_ORDER[currentAgentIdx] : null
  const currentLabel = currentAgent ? STAGE_LABELS[currentAgent] : ''
  const allDone = AGENT_ORDER.every(a => agentCompleted(events, a) === 'yes')

  const submit = useCallback(async () => {
    console.log('[BI] Submitting job:', form)
    setState({ phase: 'submitting' })
    try {
      const resp = await fetch('/api/bi/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      console.log('[BI] Submit response:', resp.status, resp.statusText)
      if (!resp.ok) {
        const body = await resp.json().catch(() => null)
        console.error('[BI] Submit failed:', resp.status, body)
        setState({ phase: 'error', message: body?.detail?.[0]?.msg || `HTTP ${resp.status}` })
        return
      }
      const data = await resp.json()
      console.log('[BI] Submit success:', data)
      setState({ phase: 'polling', jobId: data.job_id, startTime: Date.now() })
      setElapsedMs(0)
    } catch (err) {
      console.error('[BI] Submit network error:', err)
      setState({ phase: 'error', message: `Network error: ${err instanceof Error ? err.message : err}` })
    }
  }, [form])

  useEffect(() => {
    if (state.phase !== 'polling') return
    timerRef.current = setInterval(() => {
      setElapsedMs(Date.now() - (state as { startTime: number }).startTime)
    }, 1000)

    pollRef.current = setInterval(async () => {
      try {
        const resp = await fetch(`/api/bi/status/${state.jobId}`)
        console.log('[BI] Poll response:', resp.status, resp.statusText)
        if (!resp.ok) {
          const body = await resp.text().catch(() => null)
          console.error('[BI] Poll status not OK:', resp.status, body)
          setState({ phase: 'error', message: `Status check (${resp.status})`, jobId: state.jobId })
          return
        }
        const data: BIJobStatus = await resp.json()
        console.log('[BI] Poll data:', data.status, 'progress:', data.progress?.length, 'events')
        if (data.progress) {
          setEvents(data.progress as { type: string; agent: string }[])
        }
        if (data.status === 'complete') {
          console.log('[BI] Job complete!')
          setState({ phase: 'complete', jobId: state.jobId })
        } else if (data.status === 'error') {
          console.error('[BI] Job error:', data.error)
          setState({ phase: 'error', message: data.error || 'Unknown error', jobId: state.jobId })
        }
      } catch (err) {
        console.error('[BI] Poll network error:', err)
        setState({ phase: 'error', message: `Poll error: ${err instanceof Error ? err.message : err}`, jobId: state.jobId })
      }
    }, 3000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [state])

  const reset = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (timerRef.current) clearInterval(timerRef.current)
    setEvents([])
    setElapsedMs(0)
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
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-800">Analysis Progress</h2>
            <span className="text-sm text-gray-500 tabular-nums">{elapsed(elapsedMs)}</span>
          </div>

          {currentAgent && state.phase === 'polling' && !allDone && (
            <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
              <div className="flex items-center gap-3">
                <Spinner />
                <div>
                  <p className="text-sm font-medium text-indigo-800">{currentLabel}</p>
                  <p className="text-xs text-indigo-600">
                    Step {currentAgentIdx + 1} of {AGENT_ORDER.length}
                  </p>
                </div>
              </div>
            </div>
          )}

          {allDone && state.phase === 'polling' && (
            <div className="rounded-lg border border-green-200 bg-green-50 p-4">
              <div className="flex items-center gap-3">
                <Spinner />
                <p className="text-sm font-medium text-green-800">Finalizing report...</p>
              </div>
            </div>
          )}

          <ul className="space-y-1">
            {AGENT_ORDER.map((agent, i) => {
              const started = agentStarted(events, agent)
              const done = agentCompleted(events, agent)
              const active = started && done === 'no'
              const failed = done === 'failed'
              return (
                <li key={agent} className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
                  failed ? 'bg-red-50 font-medium text-red-700' :
                  active ? 'bg-indigo-50 font-medium text-indigo-700' :
                  done === 'yes' ? 'text-gray-600' :
                  'text-gray-400'
                }`}>
                  {done === 'yes' ? (
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-green-500 text-xs text-white">&#10003;</span>
                  ) : failed ? (
                    <span className="flex h-5 w-5 items-center justify-center rounded-full bg-red-500 text-xs text-white">&#10007;</span>
                  ) : active ? (
                    <span className="flex h-5 w-5 items-center justify-center">
                      <Spinner />
                    </span>
                  ) : (
                    <span className="flex h-5 w-5 items-center justify-center rounded-full border border-gray-300 text-xs text-gray-400">{i + 1}</span>
                  )}
                  <span className="flex-1">{STAGE_LABELS[agent] || agent}</span>
                  {failed && <span className="text-xs text-red-500">Failed</span>}
                </li>
              )
            })}
          </ul>

          {state.phase === 'complete' && (
            <div className="space-y-4 rounded-lg border border-green-200 bg-green-50 p-4">
              <div className="flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-green-500 text-sm text-white">&#10003;</span>
                <p className="text-sm font-semibold text-green-800">Analysis Complete</p>
              </div>
              <p className="text-xs text-green-700">Completed in {elapsed(elapsedMs)}</p>
              <div className="flex gap-3 pt-1">
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
      <div className="border-t pt-4">
        <button
          type="button"
          onClick={() => setShowModels(!showModels)}
          className="text-sm font-medium text-gray-500 hover:text-indigo-600 transition-colors"
        >
          {showModels ? '▾ Hide' : '▸ Show'} model configuration
        </button>
        {showModels && agentModels && (
          <div className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50/60 p-4 text-sm">
            <div className="mb-2 font-semibold text-gray-800">Agent Models</div>
            <div className="space-y-1">
              {Object.entries(agentModels).map(([agent, model]) => (
                <div key={agent} className="flex justify-between gap-4 py-0.5">
                  <span className="text-gray-700">{agent}</span>
                  <span className="font-mono text-xs text-indigo-700 bg-indigo-100/60 px-2 py-0.5 rounded">{model}</span>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-gray-500 italic leading-relaxed">
              Smaller models used for faster responses
            </p>
          </div>
        )}
      </div>
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
