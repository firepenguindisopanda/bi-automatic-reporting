import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { type RecentJob, useRecentJobs } from './useRecentJobs'

const STORAGE_KEY = 'bi.recentJobs'

function job(n: number, overrides: Partial<RecentJob> = {}): RecentJob {
  return { jobId: `job-${n}`, kind: 'bi', subject: `site${n}.com`, startedAt: n, ...overrides }
}

function stored(): RecentJob[] {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as RecentJob[]
}

describe('useRecentJobs', () => {
  it('starts empty when nothing is stored', () => {
    const { result } = renderHook(() => useRecentJobs())
    expect(result.current.jobs).toEqual([])
  })

  it('restores jobs saved by an earlier visit', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([job(2), job(1)]))

    const { result } = renderHook(() => useRecentJobs())

    expect(result.current.jobs.map((j) => j.jobId)).toEqual(['job-2', 'job-1'])
  })

  it.each([['{broken'], ['{"jobId":"x"}']])('treats unreadable storage %s as empty', (raw) => {
    localStorage.setItem(STORAGE_KEY, raw)
    const { result } = renderHook(() => useRecentJobs())
    expect(result.current.jobs).toEqual([])
  })

  it('remembers the newest job first and persists it', () => {
    const { result } = renderHook(() => useRecentJobs())

    act(() => result.current.remember(job(1)))
    act(() => result.current.remember(job(2, { kind: 'market_research', subject: 'EV' })))

    expect(result.current.jobs.map((j) => j.jobId)).toEqual(['job-2', 'job-1'])
    expect(stored()).toEqual(result.current.jobs)
  })

  it('moves a job to the top instead of listing it twice', () => {
    const { result } = renderHook(() => useRecentJobs())
    act(() => result.current.remember(job(1)))
    act(() => result.current.remember(job(2)))

    act(() => result.current.remember(job(1, { startedAt: 99 })))

    expect(result.current.jobs.map((j) => j.jobId)).toEqual(['job-1', 'job-2'])
    expect(result.current.jobs[0]?.startedAt).toBe(99)
  })

  it('keeps only the eight most recent jobs', () => {
    const { result } = renderHook(() => useRecentJobs())

    for (let n = 1; n <= 10; n++) act(() => result.current.remember(job(n)))

    expect(result.current.jobs).toHaveLength(8)
    expect(result.current.jobs[0]?.jobId).toBe('job-10')
    expect(result.current.jobs.at(-1)?.jobId).toBe('job-3')
    expect(stored()).toHaveLength(8)
  })

  it('forgets a job and persists the removal', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([job(2), job(1)]))
    const { result } = renderHook(() => useRecentJobs())

    act(() => result.current.forget('job-2'))

    expect(result.current.jobs.map((j) => j.jobId)).toEqual(['job-1'])
    expect(stored().map((j) => j.jobId)).toEqual(['job-1'])
  })

  it('picks up jobs started in another tab', () => {
    const { result } = renderHook(() => useRecentJobs())

    localStorage.setItem(STORAGE_KEY, JSON.stringify([job(7)]))
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'something.else' }))
    })
    expect(result.current.jobs).toEqual([])

    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }))
    })
    expect(result.current.jobs.map((j) => j.jobId)).toEqual(['job-7'])
  })
})
