import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'bi.recentJobs'
const MAX_ENTRIES = 8

export interface RecentJob {
  jobId: string
  kind: 'bi' | 'market_research'
  /** The URL analysed or the market researched - whatever names this job. */
  subject: string
  startedAt: number
}

function read(): RecentJob[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(parsed) ? (parsed as RecentJob[]) : []
  } catch {
    return []
  }
}

/**
 * Jobs this browser has started. Runs take minutes, so without this a refresh
 * or a closed tab loses the job id and the work becomes unreachable.
 */
export function useRecentJobs() {
  const [jobs, setJobs] = useState<RecentJob[]>(read)

  useEffect(() => {
    const sync = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setJobs(read())
    }
    window.addEventListener('storage', sync)
    return () => window.removeEventListener('storage', sync)
  }, [])

  const remember = useCallback((job: RecentJob) => {
    setJobs((current) => {
      const next = [job, ...current.filter((j) => j.jobId !== job.jobId)].slice(0, MAX_ENTRIES)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const forget = useCallback((jobId: string) => {
    setJobs((current) => {
      const next = current.filter((j) => j.jobId !== jobId)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  return { jobs, remember, forget }
}
