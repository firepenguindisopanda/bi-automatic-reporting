import { useCallback, useEffect, useRef, useState } from 'react'
import type { MarketResearchResultResponse } from '../types/api'

type PageState =
  | { phase: 'form' }
  | { phase: 'submitting' }
  | { phase: 'polling'; jobId: string }
  | { phase: 'complete'; data: MarketResearchResultResponse }
  | { phase: 'error'; message: string }

export default function MarketResearchPage() {
  const [query, setQuery] = useState('')
  const [state, setState] = useState<PageState>({ phase: 'form' })
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const presetIndustries = [
    { group: 'Technology', items: ['SaaS / Cloud Infrastructure', 'AI / Machine Learning Tools', 'Cybersecurity', 'DevOps / Developer Tools', 'IoT / Edge Computing'] },
    { group: 'Commerce & Finance', items: ['E-commerce / D2C Brands', 'Fintech Payments', 'InsurTech', 'Real Estate / PropTech', 'Supply Chain & Logistics'] },
    { group: 'Health & Education', items: ['HealthTech / Digital Health', 'BioTech / Life Sciences', 'EdTech Online Learning', 'Telemedicine', 'Mental Health & Wellness'] },
    { group: 'Sustainability & Mobility', items: ['Electric Vehicles / Mobility', 'Clean Energy / Renewables', 'Sustainable Packaging', 'Carbon Credit Markets', 'AgriTech / Vertical Farming'] },
  ]

  const presetQuestions = [
    { label: 'Is this market worth entering?', query: 'Market opportunity and attractiveness assessment' },
    { label: 'Who are the key competitors?', query: 'Competitive landscape and key players analysis' },
    { label: 'What are the biggest trends?', query: 'Industry trends and macro environment analysis' },
    { label: 'How big is the opportunity?', query: 'Market size TAM SAM SOM and growth potential' },
  ]

  const submit = useCallback(async (marketQuery: string) => {
    setState({ phase: 'submitting' })
    try {
      const resp = await fetch('/api/market-research/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ market_query: marketQuery }),
      })
      if (!resp.ok) {
        setState({ phase: 'error', message: `HTTP ${resp.status}` })
        return
      }
      const data = await resp.json()
      setState({ phase: 'polling', jobId: data.job_id })
    } catch {
      setState({ phase: 'error', message: 'Network error' })
    }
  }, [])

  useEffect(() => {
    if (state.phase !== 'polling') return
    pollRef.current = setInterval(async () => {
      try {
        const resp = await fetch(`/api/market-research/result/${state.jobId}`)
        if (!resp.ok) {
          setState({ phase: 'error', message: `Status check failed` })
          return
        }
        const data: MarketResearchResultResponse = await resp.json()
        if (data.status === 'complete') {
          if (pollRef.current) clearInterval(pollRef.current)
          setState({ phase: 'complete', data })
        } else if (data.status === 'error') {
          if (pollRef.current) clearInterval(pollRef.current)
          setState({ phase: 'error', message: data.error || 'Unknown error' })
        }
      } catch {
        setState({ phase: 'error', message: 'Network error during polling' })
      }
    }, 3000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [state])

  const reset = () => {
    if (pollRef.current) clearInterval(pollRef.current)
    setQuery('')
    setState({ phase: 'form' })
  }

  if (state.phase === 'complete' && state.data.result) {
    const r = state.data.result
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">Market Research Results</h1>
        <p className="text-sm text-gray-500">Market: <span className="font-medium text-gray-900">{r.market_query}</span></p>

        <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4">
          <h2 className="mb-1 text-lg font-semibold text-gray-800">Executive Summary</h2>
          <p className="text-sm text-gray-700 leading-relaxed">{r.executive_summary}</p>
        </div>

        {(r.market_size || r.growth_rate) && (
          <div className="flex gap-4">
            {r.market_size && (
              <div className="rounded-lg border border-gray-200 bg-white p-4 flex-1">
                <p className="text-xs text-gray-500 uppercase tracking-wider font-medium">Estimated Market Size</p>
                <p className="text-xl font-bold text-indigo-700 mt-1">{r.market_size}</p>
              </div>
            )}
            {r.growth_rate && (
              <div className="rounded-lg border border-gray-200 bg-white p-4 flex-1">
                <p className="text-xs text-gray-500 uppercase tracking-wider font-medium">Growth Rate</p>
                <p className="text-xl font-bold text-green-700 mt-1">{r.growth_rate}</p>
              </div>
            )}
          </div>
        )}

        {r.key_insights.length > 0 && (
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="mb-2 text-lg font-semibold text-gray-800">Key Insights</h2>
            <ul className="space-y-2">
              {r.key_insights.map((insight, i) => (
                <li key={i} className="flex gap-2 text-sm text-gray-700">
                  <span className="text-indigo-600 font-bold mt-0.5 shrink-0">{i + 1}.</span>
                  <span>{insight}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {r.recommendations.length > 0 && (
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="mb-2 text-lg font-semibold text-gray-800">Recommendations</h2>
            <ul className="space-y-2">
              {r.recommendations.map((rec, i) => (
                <li key={i} className="flex gap-2 text-sm text-gray-700">
                  <span className="text-green-600 font-bold mt-0.5 shrink-0">&rarr;</span>
                  <span>{rec}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {r.risks_and_assumptions.length > 0 && (
          <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-4">
            <h2 className="mb-2 text-lg font-semibold text-yellow-800">Risks & Assumptions</h2>
            <ul className="space-y-1">
              {r.risks_and_assumptions.map((risk, i) => (
                <li key={i} className="flex gap-2 text-sm text-yellow-800">
                  <span className="mt-0.5 shrink-0">&#9888;</span>
                  <span>{risk}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <h2 className="mb-2 text-lg font-semibold text-gray-800">Market Landscape</h2>
          <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{r.summary}</p>
        </div>

        <Section title="Key Players" resources={r.key_players} />
        <Section title="Competitor Sites" resources={r.competitor_sites} />
        <Section title="Industry Websites & Resources" resources={r.industry_websites} />
        <Section title="News & Publications" resources={r.news_sources} />
        <Section title="Research Papers & Reports" resources={r.research_papers} />
        <Section title="Communities & Forums" resources={r.relevant_communities} />

        <div className="flex gap-3">
          <a
            href={`/api/market-research/download/${state.data.job_id}?format=pdf`}
            className="rounded-md bg-indigo-700 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-800"
          >
            Download PDF
          </a>
          <a
            href={`/api/market-research/download/${state.data.job_id}?format=docx`}
            className="rounded-md border border-indigo-700 px-4 py-2 text-sm font-semibold text-indigo-700 shadow-sm hover:bg-indigo-50"
          >
            Download DOCX
          </a>
        </div>

        <button
          onClick={reset}
          className="text-sm text-indigo-600 underline hover:text-indigo-800"
        >
          Research Another Market
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Market Research</h1>
      <p className="text-gray-600">
        Research any market or industry to find key players, competitors, news sources, and resources.
        Results are processed in the background — close and come back later.
      </p>

      {(state.phase === 'form' || state.phase === 'error') && (
        <div className="space-y-6">
          <div>
            <h2 className="text-sm font-semibold text-gray-700 mb-3">Browse by Industry</h2>
            <div className="space-y-3">
              {presetIndustries.map((g) => (
                <div key={g.group}>
                  <p className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-1">{g.group}</p>
                  <div className="flex flex-wrap gap-2">
                    {g.items.map((m) => (
                      <button
                        key={m}
                        onClick={() => { setQuery(m); submit(m) }}
                        className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:border-indigo-500 hover:text-indigo-700 hover:bg-indigo-50 transition-colors"
                      >
                        {m}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-300" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="bg-gray-50 px-2 text-gray-500">or ask a strategic question</span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {presetQuestions.map((q) => (
              <button
                key={q.label}
                onClick={() => { setQuery(q.query); submit(q.query) }}
                className="rounded-md border border-indigo-200 bg-indigo-50 px-4 py-3 text-left text-sm font-medium text-indigo-800 hover:bg-indigo-100 hover:border-indigo-400 transition-colors"
              >
                <span className="block text-xs text-indigo-500 mb-0.5">STRATEGIC</span>
                {q.label}
              </button>
            ))}
          </div>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-300" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="bg-gray-50 px-2 text-gray-500">or type a custom market</span>
            </div>
          </div>

          <form
            onSubmit={(e) => { e.preventDefault(); submit(query) }}
            className="space-y-4"
          >
            <div>
              <label className="block text-sm font-medium text-gray-700">Custom market query</label>
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g., Sustainable packaging for food industry"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                required
                minLength={3}
              />
            </div>
            {state.phase === 'error' && (
              <p className="text-sm text-red-600">{state.message}</p>
            )}
            <button
              type="submit"
              disabled={query.length < 3}
              className="rounded-md bg-indigo-700 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Start Research
            </button>
          </form>
        </div>
      )}

      {state.phase === 'submitting' && (
        <div className="flex items-center gap-3 text-gray-600">
          <Spinner />
          Submitting research job...
        </div>
      )}

      {state.phase === 'polling' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 text-gray-600">
            <Spinner />
            Research in progress...
          </div>
          <p className="text-sm text-gray-500">
            This may take a minute or two. The results will appear here automatically when ready.
            You can also close this page and come back later to check.
          </p>
        </div>
      )}
    </div>
  )
}

function Section({ title, resources }: { title: string; resources: { name: string; url: string; description: string; type: string; relevance: string }[] }) {
  if (!resources.length) return null
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <h2 className="mb-3 text-lg font-semibold text-gray-800">{title}</h2>
      <div className="space-y-3">
        {resources.map((r, i) => (
          <div key={i} className="border-l-2 border-indigo-200 pl-3">
            <a
              href={r.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-indigo-700 hover:text-indigo-900 hover:underline"
            >
              {r.name}
            </a>
            <p className="text-xs text-gray-500 mt-0.5">{r.url}</p>
            <p className="text-sm text-gray-700 mt-1">{r.description}</p>
            {r.relevance && (
              <p className="text-xs text-gray-500 mt-1 italic">{r.relevance}</p>
            )}
          </div>
        ))}
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
