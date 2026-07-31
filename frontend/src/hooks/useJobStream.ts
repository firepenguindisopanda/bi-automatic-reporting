import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../lib/api'
import type { JobProgress } from '../types/api'
import { isTerminal } from '../types/api'

const POLL_INTERVAL_MS = 3000

export type Connection = 'connecting' | 'live' | 'polling' | 'closed'

interface JobStream {
  progress: JobProgress | null
  connection: Connection
  error: string | null
  /** Re-read once, e.g. after an action that changes the job. */
  refresh: () => Promise<void>
}

/**
 * Live job progress: server-sent events, with polling as a fallback.
 *
 * SSE can be blocked by a proxy that buffers responses, so a failed stream is
 * treated as normal rather than fatal - the hook drops to polling and says so,
 * and the payload is identical either way.
 */
export function useJobStream(
  kind: 'bi' | 'market_research',
  jobId: string | undefined,
): JobStream {
  const [progress, setProgress] = useState<JobProgress | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [error, setError] = useState<string | null>(null)

  // Held in refs so the effect below does not re-subscribe on every render.
  const sourceRef = useRef<EventSource | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    if (!jobId) return
    try {
      setProgress(await api.progress(kind, jobId))
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load this job.')
    }
  }, [kind, jobId])

  useEffect(() => {
    if (!jobId) {
      setConnection('closed')
      return
    }

    let cancelled = false
    setConnection('connecting')

    const stop = () => {
      sourceRef.current?.close()
      sourceRef.current = null
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = null
    }

    const accept = (next: JobProgress) => {
      if (cancelled) return
      setProgress(next)
      setError(null)
      if (isTerminal(next.status)) {
        stop()
        setConnection('closed')
      }
    }

    const startPolling = () => {
      if (cancelled || pollRef.current) return
      setConnection('polling')
      const tick = async () => {
        try {
          const next = await api.progress(kind, jobId)
          accept(next)
        } catch (e) {
          if (e instanceof ApiError && e.status === 404) {
            stop()
            setConnection('closed')
            setError('This job no longer exists.')
          }
          // Other failures are transient; the next tick will retry.
        }
      }
      void tick()
      pollRef.current = setInterval(tick, POLL_INTERVAL_MS)
    }

    // Load once immediately so the page is never blank while the stream opens.
    void api
      .progress(kind, jobId)
      .then(accept)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 404 && !cancelled) {
          setConnection('closed')
          setError('This job no longer exists.')
        }
      })

    const source = new EventSource(api.eventsUrl(kind, jobId))
    sourceRef.current = source

    source.addEventListener('progress', (event) => {
      try {
        accept(JSON.parse((event as MessageEvent).data) as JobProgress)
        if (!cancelled && !pollRef.current) setConnection('live')
      } catch {
        // A malformed frame is not worth tearing the stream down for.
      }
    })

    source.addEventListener('done', () => {
      stop()
      if (!cancelled) setConnection('closed')
    })

    source.onerror = () => {
      // EventSource retries on its own, which would race the poller; close it
      // and take over deliberately.
      source.close()
      sourceRef.current = null
      startPolling()
    }

    return () => {
      cancelled = true
      stop()
    }
  }, [kind, jobId])

  return { progress, connection, error, refresh }
}
