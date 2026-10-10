import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Callout, Card, LinkButton } from '../components/ui'
import { ApiError, api, safeHref } from '../lib/api'
import type { FiveForces, MarketResearchResult, MarketResource, Pestel, Swot } from '../types/api'

/** The finished research, read as a document rather than a dashboard. */
export default function ResearchResultPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const [result, setResult] = useState<MarketResearchResult | null>(null)
  const [hasDocuments, setHasDocuments] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!jobId) return
    api
      .researchResult(jobId)
      .then((r) => {
        if (r.result) {
          setResult(r.result)
          // `partial`: the analysis is complete but the documents never rendered.
          setHasDocuments(r.status !== 'partial')
        } else setError(r.error ?? 'This research has no result yet.')
      })
      .catch((e: unknown) =>
        setError(e instanceof ApiError ? e.message : 'Could not load the result.'),
      )
  }, [jobId])

  if (error) {
    return (
      <div className="space-y-6">
        <Callout tone="bad" title="Not available">
          {error}
        </Callout>
        <Link to={`/jobs/${jobId}?kind=market_research`} className="font-sans text-[14px] text-accent">
          Back to progress
        </Link>
      </div>
    )
  }

  if (!result) return <p className="text-muted">Loading…</p>

  return (
    <article className="space-y-9">
      <header className="border-b border-rule pb-5">
        <p className="eyebrow">Market research</p>
        <h1 className="mt-1 text-[30px] leading-tight sm:text-[34px]">{result.market_query}</h1>
      </header>

      <section>
        <h2 className="eyebrow">Executive summary</h2>
        <p className="mt-2 max-w-[62ch] text-[18px] leading-relaxed">{result.executive_summary}</p>
      </section>

      {result.market_size || result.growth_rate ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {result.market_size ? <Figure label="Market size" value={result.market_size} /> : null}
          {result.growth_rate ? <Figure label="Growth" value={result.growth_rate} /> : null}
        </div>
      ) : null}

      <NumberedList title="Key insights" items={result.key_insights} />
      <NumberedList title="Recommendations" items={result.recommendations} />

      {result.risks_and_assumptions.length > 0 ? (
        <section>
          <h2 className="eyebrow">Risks and assumptions</h2>
          <ul className="mt-2 space-y-2 border-l-2 border-warning pl-4">
            {result.risks_and_assumptions.map((risk, i) => (
              <li key={i} className="max-w-[62ch] text-[16px] text-muted">
                {risk}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {result.summary ? (
        <section>
          <h2 className="eyebrow">Landscape</h2>
          <p className="mt-2 max-w-[62ch] whitespace-pre-line text-[17px] leading-relaxed">
            {result.summary}
          </p>
        </section>
      ) : null}

      {result.swot ? <SwotGrid swot={result.swot} /> : null}
      {result.pestel ? <PestelSection pestel={result.pestel} /> : null}
      {result.five_forces ? <FiveForcesSection forces={result.five_forces} /> : null}

      <Resources title="Key players" items={result.key_players} />
      <Resources title="Competitors" items={result.competitor_sites} />
      <Resources title="Industry sources" items={result.industry_websites} />
      <Resources title="News and publications" items={result.news_sources} />
      <Resources title="Research" items={result.research_papers} />
      <Resources title="Communities" items={result.relevant_communities} />

      {hasDocuments ? (
        <div className="flex flex-wrap gap-3 border-t border-rule pt-6">
          <LinkButton href={api.downloadUrl('market_research', jobId ?? '', 'pdf')}>
            Download PDF
          </LinkButton>
          <LinkButton
            href={api.downloadUrl('market_research', jobId ?? '', 'docx')}
            variant="secondary"
          >
            Download Word
          </LinkButton>
        </div>
      ) : (
        <p className="border-t border-rule pt-6 text-[15px] text-muted">
          The PDF and Word versions could not be produced for this research.
        </p>
      )}
    </article>
  )
}

/** A headline figure.
 *
 * Market size arrives as "TAM: … · SAM: … · SOM: …" now that the sizing stage
 * keeps all three, and at display size that wrapped into a fifteen-line column.
 * Split it into its parts instead; a single figure is left as it was.
 */
function Figure({ label, value }: { label: string; value: string }) {
  const parts = value.split(' · ').filter(Boolean)

  return (
    <Card className="p-4">
      <p className="eyebrow">{label}</p>
      {parts.length > 1 ? (
        <ul className="mt-2 space-y-1.5">
          {parts.map((part, i) => (
            <li key={i} className="text-[15px] leading-snug text-accent">
              {part}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-[22px] leading-snug text-accent">{value}</p>
      )}
    </Card>
  )
}

/** Numbered because these are ranked findings, not an unordered set. */
function NumberedList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <section>
      <h2 className="eyebrow">{title}</h2>
      <ol className="mt-2 space-y-2.5">
        {items.map((item, i) => (
          <li key={i} className="flex max-w-[62ch] gap-3 text-[16px]">
            <span className="numeric shrink-0 pt-1 text-accent">
              {String(i + 1).padStart(2, '0')}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

function Resources({ title, items }: { title: string; items: MarketResource[] }) {
  if (items.length === 0) return null
  return (
    <section>
      <h2 className="eyebrow">{title}</h2>
      {/* Two columns once there is room: six of these lists stacked one entry
          deep made the page far longer than the reading in it. */}
      <ul className="mt-2 grid border-t border-rule md:grid-cols-2 md:gap-x-8">
        {items.map((item, i) => (
          <li key={i} className="border-b border-rule py-3">
            <a
              href={safeHref(item.url)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[17px] text-accent no-underline hover:underline"
            >
              {item.name}
            </a>
            {/* The address was shown but not clickable, which reads as a broken
                link rather than a caption. */}
            <a
              href={safeHref(item.url)}
              target="_blank"
              rel="noopener noreferrer"
              className="numeric mt-0.5 block break-all py-0.5 text-faint no-underline
                         hover:text-accent hover:underline"
            >
              {item.url}
            </a>
            {item.description ? (
              <p className="mt-1 max-w-[62ch] text-[15px]">{item.description}</p>
            ) : null}
            {item.relevance ? (
              <p className="mt-1 max-w-[62ch] text-[14px] text-muted italic">{item.relevance}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}

function SwotGrid({ swot }: { swot: Swot }) {
  const quadrants: [string, string[]][] = [
    ['Strengths', swot.strengths],
    ['Weaknesses', swot.weaknesses],
    ['Opportunities', swot.opportunities],
    ['Threats', swot.threats],
  ]
  return (
    <section>
      <h2 className="eyebrow">Market SWOT</h2>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        {quadrants.map(([label, items]) => (
          <Card key={label} className="p-4">
            <h3 className="eyebrow">{label}</h3>
            <ul className="mt-2 space-y-1.5">
              {items.map((item, i) => (
                <li key={i} className="text-[15px]">
                  {item}
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </section>
  )
}

/** Label-and-entries rows, shared by the PESTEL and five-forces sections. */
function Rows({ title, rows }: { title: string; rows: [string, string[]][] }) {
  const present = rows.filter(([, items]) => items.some(Boolean))
  if (present.length === 0) return null
  return (
    <section>
      <h2 className="eyebrow">{title}</h2>
      <dl className="mt-2 border-t border-rule">
        {present.map(([label, items]) => (
          <div key={label} className="grid gap-1 border-b border-rule py-3 sm:grid-cols-[10rem_1fr]">
            <dt className="font-sans text-[14px] text-muted">{label}</dt>
            <dd>
              <ul className="space-y-1">
                {items.filter(Boolean).map((item, i) => (
                  <li key={i} className="max-w-[62ch] text-[15px]">
                    {item}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function PestelSection({ pestel }: { pestel: Pestel }) {
  return (
    <Rows
      title="PESTEL"
      rows={[
        ['Political', pestel.political],
        ['Economic', pestel.economic],
        ['Social', pestel.social],
        ['Technological', pestel.technological],
        ['Environmental', pestel.environmental],
        ['Legal', pestel.legal],
      ]}
    />
  )
}

function FiveForcesSection({ forces }: { forces: FiveForces }) {
  return (
    <Rows
      title="Five forces"
      rows={[
        ['Threat of new entrants', [forces.threat_of_new_entrants]],
        ['Supplier power', [forces.supplier_power]],
        ['Buyer power', [forces.buyer_power]],
        ['Threat of substitutes', [forces.threat_of_substitutes]],
        ['Competitive rivalry', [forces.competitive_rivalry]],
      ]}
    />
  )
}
