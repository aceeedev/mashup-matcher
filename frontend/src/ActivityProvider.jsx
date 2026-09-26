import { useCallback, useEffect, useState } from 'react'
import { ActivityContext } from './activity'
import { api } from './api'

const BUSY_INTERVAL_MS = 1500
const IDLE_INTERVAL_MS = 5000

/** Polls /api/enrichment/activity for the whole app - fast while a job is running or
 * the search engines are blocked, slower when idle - so every page reads one shared copy. */
export default function ActivityProvider({ children }) {
  const [activity, setActivity] = useState(null)
  const [error, setError] = useState(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    let timer

    const tick = async () => {
      let busy = false
      try {
        const data = await api.activity()
        if (cancelled) return
        setActivity({ ...data, receivedAt: Date.now() })
        setError(null)
        busy = data.active.length > 0 || data.throttle.blocked
      } catch (err) {
        if (cancelled) return
        setError(err.message)
      }
      timer = setTimeout(tick, busy ? BUSY_INTERVAL_MS : IDLE_INTERVAL_MS)
    }
    tick()

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [nonce])

  // Poll immediately (e.g. right after starting a job) instead of waiting for the next tick
  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  return <ActivityContext.Provider value={{ activity, error, refresh }}>{children}</ActivityContext.Provider>
}
