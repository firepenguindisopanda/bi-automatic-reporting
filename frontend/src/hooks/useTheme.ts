import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'bi.theme'

function read(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'light' || stored === 'dark' ? stored : 'system'
}

/**
 * Theme preference. `system` removes the attribute entirely so the CSS
 * `prefers-color-scheme` rules take over, rather than pinning a value that
 * would stop tracking the OS.
 */
export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void; cycle: () => void } {
  const [theme, setThemeState] = useState<Theme>(read)

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') {
      root.removeAttribute('data-theme')
      localStorage.removeItem(STORAGE_KEY)
    } else {
      root.setAttribute('data-theme', theme)
      localStorage.setItem(STORAGE_KEY, theme)
    }
  }, [theme])

  const cycle = useCallback(() => {
    setThemeState((current) =>
      current === 'system' ? 'light' : current === 'light' ? 'dark' : 'system',
    )
  }, [])

  return { theme, setTheme: setThemeState, cycle }
}
