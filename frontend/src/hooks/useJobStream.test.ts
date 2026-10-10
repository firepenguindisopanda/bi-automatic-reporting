import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../lib/api'
import { FakeEventSource, installFakeEventSource } from '../test/fakeEventSource'
import { makeProgress, makeStage } from '../test/fixtures'
import { useJobStream } from './useJobStream'

const running = makeProgress({
  status: 'running',
  stages: [makeStage({ stage: 'scrape', status: 'running' })],
})

beforeEach(() => {
  installFakeEventSource()
})

/**
 * Polling tests fake setInterval, which Testing Library's waitFor also relies
 * on; they settle pending promises by advancing the fake clock instead.
 */
async function flush(): Promise<void> {
  await act(() => vi.advanceTimersByTimeAsync(0))
}

/** Render the hook and wait for the initial progress read to land. */
async function renderStream(progressSpy = vi.spyOn(api, 'progress').mockResolvedValue(running)) {
  const hook = renderHook(() => useJobStream('bi', 'job-123'))
  if (vi.isFakeTimers()) await flush()
  else await waitFor(() => expect(hook.result.current.progress).not.toBeNull())
  expect(hook.result.current.progress).toEqual(running)
  return { ...hook, progressSpy, source: FakeEventSource.latest() }
}

function fakeIntervals(): void {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
}

describe('useJobStream', () => {
  it('loads once immediately and subscribes to the job event stream', async () => {
    const { result, source, progressSpy } = await renderStream()

    expect(progressSpy).toHaveBeenCalledWith('bi', 'job-123')
    expect(source.url).toBe('/api/jobs/job-123/events?kind=bi')
    expect(result.current.progress).toEqual(running)
    expect(result.current.connection).toBe('connecting')
  })

  it('applies each progress frame and reports the connection as live', async () => {
    const { result, source } = await renderStream()
    const next = makeProgress({
      status: 'running',
      elapsed_ms: 4200,
      stages: [
        makeStage({ stage: 'scrape', status: 'succeeded', duration_ms: 4100 }),
        makeStage({ stage: 'profile', seq: 2, status: 'running' }),
      ],
    })

    act(() => source.emit('progress', next))

    expect(result.current.progress).toEqual(next)
    expect(result.current.connection).toBe('live')
  })

  it('ignores a malformed frame without dropping the stream', async () => {
    const { result, source } = await renderStream()

    act(() => source.emit('progress', '{not json'))

    expect(result.current.progress).toEqual(running)
    expect(source.closed).toBe(false)
  })

  it('closes the stream on the done event', async () => {
    const { result, source } = await renderStream()

    act(() => source.emit('done', ''))

    expect(source.closed).toBe(true)
    expect(result.current.connection).toBe('closed')
  })

  it('closes the stream as soon as a frame carries a terminal status', async () => {
    const { result, source } = await renderStream()

    act(() => source.emit('progress', makeProgress({ status: 'completed' })))

    expect(source.closed).toBe(true)
    expect(result.current.connection).toBe('closed')
    expect(result.current.progress?.status).toBe('completed')
  })

  it('never lets a slow first read overwrite newer frames from the stream', async () => {
    let answerFirstRead: (p: typeof running) => void = () => {}
    vi.spyOn(api, 'progress').mockReturnValue(
      new Promise((resolve) => {
        answerFirstRead = resolve
      }),
    )
    const { result } = renderHook(() => useJobStream('bi', 'job-123'))
    const source = FakeEventSource.latest()

    act(() => source.emit('progress', makeProgress({ status: 'completed' })))
    await act(async () => answerFirstRead(running))

    expect(result.current.progress?.status).toBe('completed')
    expect(result.current.connection).toBe('closed')
  })

  it('falls back to polling when the stream errors, until the job settles', async () => {
    fakeIntervals()
    const { result, source, progressSpy } = await renderStream()
    progressSpy.mockClear()

    act(() => source.fail())

    expect(source.closed).toBe(true)
    expect(result.current.connection).toBe('polling')
    // One read straight away so the switch is seamless...
    expect(progressSpy).toHaveBeenCalledTimes(1)

    // ...then one per interval.
    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(progressSpy).toHaveBeenCalledTimes(2)

    progressSpy.mockResolvedValue(makeProgress({ status: 'completed' }))
    await act(() => vi.advanceTimersByTimeAsync(3000))
    expect(result.current.progress?.status).toBe('completed')
    expect(result.current.connection).toBe('closed')

    // A settled job is not polled again.
    await act(() => vi.advanceTimersByTimeAsync(9000))
    expect(progressSpy).toHaveBeenCalledTimes(3)
  })

  it('keeps polling through transient failures', async () => {
    fakeIntervals()
    const { result, source, progressSpy } = await renderStream()
    progressSpy.mockClear()
    progressSpy.mockRejectedValue(new ApiError('The server returned 502.', 502))

    act(() => source.fail())
    await act(() => vi.advanceTimersByTimeAsync(6000))

    expect(progressSpy).toHaveBeenCalledTimes(3)
    expect(result.current.connection).toBe('polling')
    expect(result.current.error).toBeNull()
  })

  it('reports a job that does not exist and closes the stream', async () => {
    vi.spyOn(api, 'progress').mockRejectedValue(new ApiError('Not found.', 404))

    const { result } = renderHook(() => useJobStream('bi', 'gone'))

    await waitFor(() => expect(result.current.error).toBe('This job no longer exists.'))
    expect(result.current.connection).toBe('closed')
    expect(result.current.progress).toBeNull()
    expect(FakeEventSource.latest().closed).toBe(true)
  })

  it('stops polling when the job disappears mid-run', async () => {
    fakeIntervals()
    const { result, source, progressSpy } = await renderStream()
    progressSpy.mockClear()
    progressSpy.mockRejectedValue(new ApiError('Not found.', 404))

    act(() => source.fail())
    await flush()

    expect(result.current.error).toBe('This job no longer exists.')
    expect(result.current.connection).toBe('closed')

    await act(() => vi.advanceTimersByTimeAsync(9000))
    expect(progressSpy).toHaveBeenCalledTimes(1)
  })

  it('refresh re-reads the job and surfaces a failure message', async () => {
    const { result, progressSpy } = await renderStream()
    progressSpy.mockRejectedValueOnce(new ApiError('Too many requests.', 429))

    await act(() => result.current.refresh())
    expect(result.current.error).toBe('Too many requests.')

    const settled = makeProgress({ status: 'partial' })
    progressSpy.mockResolvedValueOnce(settled)
    await act(() => result.current.refresh())
    expect(result.current.error).toBeNull()
    expect(result.current.progress).toEqual(settled)
  })

  it('closes everything on unmount', async () => {
    fakeIntervals()
    const { source, progressSpy, unmount } = await renderStream()
    act(() => source.fail())
    expect(progressSpy).toHaveBeenCalledTimes(2)

    unmount()
    await vi.advanceTimersByTimeAsync(9000)

    expect(source.closed).toBe(true)
    expect(progressSpy).toHaveBeenCalledTimes(2)
  })

  it('does nothing without a job id', () => {
    const progressSpy = vi.spyOn(api, 'progress')

    const { result } = renderHook(() => useJobStream('bi', undefined))

    expect(result.current.connection).toBe('closed')
    expect(progressSpy).not.toHaveBeenCalled()
    expect(FakeEventSource.instances).toHaveLength(0)
  })
})
