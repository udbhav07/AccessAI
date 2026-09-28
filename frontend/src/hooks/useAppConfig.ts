// Loads /api/config once, to learn whether the server is up and whether AI is on.

import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { AppConfig } from '../api/types'

interface AppConfigState {
  config: AppConfig | null
  error: string | null
}

export function useAppConfig(): AppConfigState {
  const [state, setState] = useState<AppConfigState>({ config: null, error: null })

  useEffect(() => {
    let cancelled = false
    api
      .getConfig()
      .then((config) => !cancelled && setState({ config, error: null }))
      .catch((err: Error) => !cancelled && setState({ config: null, error: err.message }))
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
