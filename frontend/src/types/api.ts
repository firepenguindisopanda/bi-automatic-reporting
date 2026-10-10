export type StageStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'

export type JobStatus =
  | 'queued'
  | 'pending'
  | 'running'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'interrupted'

/** One stage's live state. Mirrors app/models/bi.py:StageReport. */
export interface StageReport {
  stage: string
  display: string
  label: string
  seq: number
  status: StageStatus
  duration_ms: number | null
  model: string | null
  attempts: number
  prompt_tokens: number | null
  completion_tokens: number | null
  completeness: number | null
  confidence: number | null
  critic_note: string | null
  warnings: string[]
  error: string | null
  error_kind: string | null
}

/** Everything needed to render a job after a refresh. */
export interface JobProgress {
  job_id: string
  kind: 'bi' | 'market_research'
  status: JobStatus
  error: string | null
  /** The URL analysed or the market researched. Null on older jobs. */
  subject: string | null
  stages: StageReport[]
  elapsed_ms: number
  eta_ms: number | null
  quality: number | null
  confidence: number | null
  queue_position: number | null
}

export interface SubmitResponse {
  job_id: string
  status: string
  message: string
}

export interface ResendResponse {
  job_id: string
  sent: boolean
  detail: string
}

export interface RetryResponse {
  job_id: string
  status: JobStatus
}

export interface MarketResource {
  name: string
  url: string
  description: string
  type: string
  relevance: string
}

export interface MarketResearchResult {
  market_query: string
  executive_summary: string
  key_insights: string[]
  recommendations: string[]
  risks_and_assumptions: string[]
  summary: string
  market_size: string
  growth_rate: string
  key_players: MarketResource[]
  news_sources: MarketResource[]
  industry_websites: MarketResource[]
  competitor_sites: MarketResource[]
  research_papers: MarketResource[]
  relevant_communities: MarketResource[]
  /** Carried through from the macro and sizing stages. Absent on older results. */
  pestel?: Pestel | null
  five_forces?: FiveForces | null
  swot?: Swot | null
}

export interface Pestel {
  political: string[]
  economic: string[]
  social: string[]
  technological: string[]
  environmental: string[]
  legal: string[]
}

export interface FiveForces {
  threat_of_new_entrants: string
  supplier_power: string
  buyer_power: string
  threat_of_substitutes: string
  competitive_rivalry: string
}

export interface Swot {
  strengths: string[]
  weaknesses: string[]
  opportunities: string[]
  threats: string[]
}

export interface MarketResearchResultResponse {
  job_id: string
  status: JobStatus
  result: MarketResearchResult | null
  error: string | null
}

export type AgentModels = Record<string, string>

const TERMINAL_STATUSES: readonly JobStatus[] = [
  'completed',
  'partial',
  'failed',
  'interrupted',
]

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(status)
}
