// Used by ResultActions to disable Verify and Download once a result has expired.

import { useEffect, useState } from 'react'

/** True once `expiresAt` (unix seconds) has passed; re-renders at that moment. */
export function useExpired(expiresAt: number): boolean {
  const [now, setNow] = useState(() => Date.now())
  const expiresMs = expiresAt * 1000

  useEffect(() => {
    if (now >= expiresMs) return
    const timer = setTimeout(() => setNow(Date.now()), expiresMs - now)
    return () => clearTimeout(timer)
  }, [now, expiresMs])

  return now >= expiresMs
}
