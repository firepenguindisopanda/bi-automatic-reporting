export interface BISubmitResponse {
  job_id: string
  status: string
  message: string
}

export interface BIJobStatus {
  job_id: string
  status: string
  progress: { type: string; agent: string }[]
  error: string | null
}

export type AgentModels = Record<string, string>

export interface MarketResearchSubmitResponse {
  job_id: string
  status: string
  message: string
}

export interface MarketResearchStatusResponse {
  job_id: string
  status: string
  error: string | null
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
}

export interface MarketResearchResultResponse {
  job_id: string
  status: string
  result: MarketResearchResult | null
  error: string | null
}
