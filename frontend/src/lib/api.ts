import type {
  AgentModels,
  JobProgress,
  MarketResearchResultResponse,
  ResendResponse,
  SubmitResponse,
} from '../types/api'

/** An API failure with a message worth showing someone. */
export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(path, init)
  } catch {
    throw new ApiError('Could not reach the server. Check your connection.', 0)
  }

  if (!resp.ok) {
    throw new ApiError(await readErrorDetail(resp), resp.status)
  }
  return (await resp.json()) as T
}

/** FastAPI reports validation errors as a list; flatten to one readable line. */
async function readErrorDetail(resp: Response): Promise<string> {
  try {
    const body = await resp.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      return detail.map((d: { msg?: string }) => d.msg ?? '').filter(Boolean).join('; ')
    }
  } catch {
    // fall through to the status-based message
  }
  if (resp.status === 429) return 'Too many requests. Wait a moment and try again.'
  if (resp.status === 404) return 'Not found.'
  return `The server returned ${resp.status}.`
}

export const api = {
  submitAnalysis: (url: string, email: string) =>
    request<SubmitResponse>('/api/bi/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, email }),
    }),

  submitResearch: (marketQuery: string) =>
    request<SubmitResponse>('/api/market-research/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market_query: marketQuery }),
    }),

  progress: (kind: 'bi' | 'market_research', jobId: string) =>
    request<JobProgress>(
      kind === 'bi'
        ? `/api/bi/progress/${jobId}`
        : `/api/market-research/progress/${jobId}`,
    ),

  researchResult: (jobId: string) =>
    request<MarketResearchResultResponse>(`/api/market-research/result/${jobId}`),

  resend: (jobId: string) =>
    request<ResendResponse>(`/api/bi/${jobId}/resend`, { method: 'POST' }),

  models: () => request<AgentModels>('/api/bi/models'),

  downloadUrl: (kind: 'bi' | 'market_research', jobId: string, format: 'pdf' | 'docx') =>
    kind === 'bi'
      ? `/api/bi/download/${jobId}?format=${format}`
      : `/api/market-research/download/${jobId}?format=${format}`,

  eventsUrl: (kind: 'bi' | 'market_research', jobId: string) =>
    `/api/jobs/${jobId}/events?kind=${kind}`,
}

/** Accept "acme.com" and turn it into something the backend will accept. */
export function normalizeUrl(input: string): string {
  const trimmed = input.trim()
  if (!trimmed) return ''
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return `https://${trimmed}`
}

export function looksLikeUrl(input: string): boolean {
  const candidate = normalizeUrl(input)
  if (!candidate) return false
  try {
    const parsed = new URL(candidate)
    return parsed.hostname.includes('.') && parsed.hostname.length > 3
  } catch {
    return false
  }
}

export function looksLikeEmail(input: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(input.trim())
}
