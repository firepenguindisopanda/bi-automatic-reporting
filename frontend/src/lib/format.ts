/** Duration for a folio column: compact, and never wider than it needs to be. */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds % 60)}s`
}

/** Coarser phrasing for a running total, where tenths would be noise. */
export function elapsed(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`
}

export function eta(ms: number | null): string | null {
  if (ms === null || ms <= 0) return null
  const minutes = Math.round(ms / 60000)
  if (minutes >= 2) return `about ${minutes} minutes left`
  if (ms > 45000) return 'about a minute left'
  return 'less than a minute left'
}

/** Strip the vendor prefix: "meta/llama-3.1-8b-instruct" reads as the model. */
export function modelName(model: string | null): string {
  if (!model) return ''
  const slash = model.lastIndexOf('/')
  return slash >= 0 ? model.slice(slash + 1) : model
}

export function tokens(n: number | null | undefined): string {
  if (!n) return '—'
  if (n < 1000) return String(n)
  return `${(n / 1000).toFixed(1)}k`
}

export function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}
