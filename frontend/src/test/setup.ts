import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Vitest runs without globals, so Testing Library cannot register its own
// cleanup; unmount explicitly so one test's tree never leaks into the next.
afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.useRealTimers()
})
