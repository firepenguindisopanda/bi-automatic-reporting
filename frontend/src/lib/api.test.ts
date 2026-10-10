import { beforeEach, describe, expect, it, vi } from 'vitest'
import { jsonResponse, makeProgress } from '../test/fixtures'
import { ApiError, api, looksLikeEmail, looksLikeUrl, normalizeUrl, safeHref } from './api'

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

/** The init passed to the n-th fetch call, with its JSON body decoded. */
function sentRequest(call = 0): { path: string; init: RequestInit | undefined; body: unknown } {
  const args = fetchMock.mock.calls[call]
  if (!args) throw new Error(`fetch was not called ${call + 1} time(s)`)
  const [input, init] = args
  return {
    path: String(input),
    init,
    body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
  }
}

async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (e) {
    if (e instanceof ApiError) return e
    throw e
  }
  throw new Error('Expected the request to fail')
}

describe('api requests', () => {
  it('submits an analysis as JSON and returns the parsed reply', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ job_id: 'j1', status: 'queued', message: 'ok' }))

    const reply = await api.submitAnalysis('https://acme.com', 'me@acme.com')

    expect(reply).toEqual({ job_id: 'j1', status: 'queued', message: 'ok' })
    const { path, init, body } = sentRequest()
    expect(path).toBe('/api/bi/submit')
    expect(init?.method).toBe('POST')
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(body).toEqual({ url: 'https://acme.com', email: 'me@acme.com' })
  })

  it('submits market research under the field name the backend expects', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ job_id: 'r1', status: 'queued', message: '' }))

    await api.submitResearch('EV charging in Europe')

    const { path, body } = sentRequest()
    expect(path).toBe('/api/market-research/submit')
    expect(body).toEqual({ market_query: 'EV charging in Europe' })
  })

  it('reads progress from the endpoint matching the job kind', async () => {
    fetchMock.mockImplementation(async () => jsonResponse(makeProgress()))

    await api.progress('bi', 'j1')
    await api.progress('market_research', 'r1')

    expect(sentRequest(0).path).toBe('/api/bi/progress/j1')
    expect(sentRequest(1).path).toBe('/api/market-research/progress/r1')
  })

  it('posts a resend for the job', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ job_id: 'j1', sent: true, detail: 'Sent' }))

    const reply = await api.resend('j1')

    expect(reply.sent).toBe(true)
    expect(sentRequest().path).toBe('/api/bi/j1/resend')
    expect(sentRequest().init?.method).toBe('POST')
  })
})

describe('api errors', () => {
  it('surfaces a string detail from the server', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'That URL could not be fetched.' }, 400))

    const error = await rejection(api.submitAnalysis('https://acme.com', 'me@acme.com'))

    expect(error.message).toBe('That URL could not be fetched.')
    expect(error.status).toBe(400)
  })

  it('flattens FastAPI validation errors into one line', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: [{ msg: 'url is invalid' }, { msg: 'email is invalid' }, {}] }, 422),
    )

    const error = await rejection(api.submitAnalysis('x', 'y'))

    expect(error.message).toBe('url is invalid; email is invalid')
    expect(error.status).toBe(422)
  })

  it.each([
    [429, 'Too many requests. Wait a moment and try again.'],
    [404, 'Not found.'],
    [502, 'The server returned 502.'],
  ])('falls back to a status message for a %i without a JSON body', async (status, message) => {
    fetchMock.mockResolvedValue(new Response('<html>gateway</html>', { status }))

    const error = await rejection(api.progress('bi', 'j1'))

    expect(error.message).toBe(message)
    expect(error.status).toBe(status)
  })

  it('reports a network failure as status 0', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await rejection(api.models())

    expect(error.status).toBe(0)
    expect(error.message).toBe('Could not reach the server. Check your connection.')
  })
})

describe('api URLs', () => {
  it('builds download URLs per kind and format', () => {
    expect(api.downloadUrl('bi', 'j1', 'pdf')).toBe('/api/bi/download/j1?format=pdf')
    expect(api.downloadUrl('market_research', 'r1', 'docx')).toBe(
      '/api/market-research/download/r1?format=docx',
    )
  })

  it('builds the event stream URL with the kind as a query', () => {
    expect(api.eventsUrl('bi', 'j1')).toBe('/api/jobs/j1/events?kind=bi')
    expect(api.eventsUrl('market_research', 'r1')).toBe('/api/jobs/r1/events?kind=market_research')
  })
})

describe('input helpers', () => {
  it('normalizes a bare domain to https and leaves schemes alone', () => {
    expect(normalizeUrl('  acme.com  ')).toBe('https://acme.com')
    expect(normalizeUrl('HTTP://acme.com')).toBe('HTTP://acme.com')
    expect(normalizeUrl('   ')).toBe('')
  })

  it('accepts addresses with a real-looking host only', () => {
    expect(looksLikeUrl('acme.com')).toBe(true)
    expect(looksLikeUrl('https://shop.acme.co.uk/path')).toBe(true)
    expect(looksLikeUrl('acme')).toBe(false)
    expect(looksLikeUrl('a.b')).toBe(false)
    expect(looksLikeUrl('')).toBe(false)
  })

  it('accepts plausible email addresses only', () => {
    expect(looksLikeEmail(' me@acme.com ')).toBe(true)
    expect(looksLikeEmail('me@acme.c')).toBe(false)
    expect(looksLikeEmail('me @acme.com')).toBe(false)
    expect(looksLikeEmail('acme.com')).toBe(false)
  })
})

describe('safeHref', () => {
  it('passes plain web links through', () => {
    expect(safeHref('https://example.com/a?b=1')).toBe('https://example.com/a?b=1')
    expect(safeHref('http://example.com')).toBe('http://example.com/')
  })

  it('refuses every other scheme, and junk', () => {
    expect(safeHref('javascript:alert(1)')).toBeUndefined()
    expect(safeHref('data:text/html,<script>alert(1)</script>')).toBeUndefined()
    expect(safeHref('file:///etc/passwd')).toBeUndefined()
    expect(safeHref('not a url')).toBeUndefined()
  })
})
