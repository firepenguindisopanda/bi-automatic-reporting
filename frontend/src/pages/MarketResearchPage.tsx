import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Callout, Card, Field } from '../components/ui'
import { useRecentJobs } from '../hooks/useRecentJobs'
import { ApiError, api } from '../lib/api'

const INDUSTRIES: { group: string; items: string[] }[] = [
  {
    group: 'Technology',
    items: [
      'SaaS / cloud infrastructure',
      'AI / machine learning tools',
      'Cybersecurity',
      'Developer tools',
    ],
  },
  {
    group: 'Commerce & finance',
    items: ['E-commerce / D2C brands', 'Fintech payments', 'InsurTech', 'Supply chain & logistics'],
  },
  {
    group: 'Health & education',
    items: ['Digital health', 'BioTech / life sciences', 'Online learning', 'Telemedicine'],
  },
  {
    group: 'Sustainability & mobility',
    items: ['Electric vehicles', 'Clean energy', 'Sustainable packaging', 'Vertical farming'],
  },
]

const QUESTIONS = [
  { label: 'Is this market worth entering?', query: 'Market opportunity and attractiveness assessment' },
  { label: 'Who are the key competitors?', query: 'Competitive landscape and key players analysis' },
  { label: 'What are the biggest trends?', query: 'Industry trends and macro environment analysis' },
  { label: 'How big is the opportunity?', query: 'Market size TAM SAM SOM and growth potential' },
]

export default function MarketResearchPage() {
  const navigate = useNavigate()
  const { remember } = useRecentJobs()
  const [query, setQuery] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = async (marketQuery: string) => {
    if (marketQuery.trim().length < 3 || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const job = await api.submitResearch(marketQuery.trim())
      remember({
        jobId: job.job_id,
        kind: 'market_research',
        subject: marketQuery.trim(),
        startedAt: Date.now(),
      })
      navigate(`/jobs/${job.job_id}?kind=market_research`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the research.')
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-10">
      <section>
        <p className="eyebrow">Market research</p>
        <h1 className="mt-1 text-[32px] leading-tight sm:text-[38px]">
          Understand a market before you enter it
        </h1>
        <p className="mt-3 max-w-[52ch] text-[17px] text-muted">
          Scans the macro environment and industry structure, sizes the opportunity, and gathers
          the players and sources worth reading. Runs in three passes, each one building on the last.
        </p>
      </section>

      <Card className="p-5 sm:p-6">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void start(query)
          }}
          className="space-y-5"
        >
          <Field
            label="Market"
            type="text"
            placeholder="Sustainable packaging for food producers"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            hint="Be as specific as you can. A narrower market gets a sharper answer."
          />
          {error ? (
            <Callout tone="bad" title="Could not start">
              {error}
            </Callout>
          ) : null}
          <Button type="submit" disabled={query.trim().length < 3 || submitting}>
            {submitting ? 'Starting…' : 'Start research'}
          </Button>
        </form>
      </Card>

      <section>
        <h2 className="eyebrow">Or ask a strategic question</h2>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {QUESTIONS.map((q) => (
            <button
              key={q.label}
              type="button"
              disabled={submitting}
              onClick={() => void start(q.query)}
              className="rounded-card border border-rule bg-surface px-4 py-3 text-left text-[16px]
                         shadow-card transition-colors hover:border-accent hover:text-accent
                         disabled:opacity-45"
            >
              {q.label}
            </button>
          ))}
        </div>
      </section>

      <section>
        <h2 className="eyebrow">Or pick an industry</h2>
        <div className="mt-3 space-y-4">
          {INDUSTRIES.map((group) => (
            <div key={group.group}>
              <p className="font-sans text-[12px] text-faint">{group.group}</p>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {group.items.map((item) => (
                  <button
                    key={item}
                    type="button"
                    disabled={submitting}
                    onClick={() => void start(item)}
                    className="min-h-11 rounded-sm border border-rule-strong px-3 font-sans
                               text-[13px] transition-colors hover:border-accent hover:text-accent
                               disabled:opacity-45"
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
