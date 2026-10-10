import { vi } from 'vitest'

type Listener = (event: MessageEvent) => void

/**
 * Just enough of EventSource for useJobStream: named listeners, onerror and
 * close(). Tests drive it by hand, so frames arrive exactly when a test says.
 */
export class FakeEventSource {
  static instances: FakeEventSource[] = []

  /** The most recently opened stream; throws if none was opened. */
  static latest(): FakeEventSource {
    const source = FakeEventSource.instances.at(-1)
    if (!source) throw new Error('No EventSource was opened')
    return source
  }

  readonly url: string
  closed = false
  onerror: ((event: Event) => void) | null = null
  private readonly listeners = new Map<string, Listener[]>()

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close(): void {
    this.closed = true
  }

  /** Deliver a named frame. A closed stream delivers nothing, like the real one. */
  emit(type: string, data: unknown): void {
    if (this.closed) return
    const payload = typeof data === 'string' ? data : JSON.stringify(data)
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: payload }))
    }
  }

  /** Simulate the connection dropping (or a proxy refusing to stream). */
  fail(): void {
    this.onerror?.(new Event('error'))
  }
}

export function installFakeEventSource(): void {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
}
