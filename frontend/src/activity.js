import { createContext, useContext, useEffect, useState } from 'react'

// Shared enrichment activity (running/queued/recent jobs, throttle state, status counts),
// polled once for the whole app by <ActivityProvider>. Read it with useActivity().
export const ActivityContext = createContext({ activity: null, error: null, refresh: () => {} })

export function useActivity() {
  return useContext(ActivityContext)
}

/** Converts a server epoch (seconds) to a local ms timestamp, correcting for clock skew
 * between the browser and the containers using the server_time sent with each poll. */
export function toLocalMs(activity, epochSeconds) {
  if (!activity || epochSeconds == null) return null
  return activity.receivedAt + (epochSeconds - activity.server_time) * 1000
}

/** Whole seconds remaining until `targetMs` (a local timestamp), ticking live. */
export function useCountdown(targetMs) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!targetMs) return
    const timer = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(timer)
  }, [targetMs])

  return targetMs ? Math.max(0, Math.ceil((targetMs - now) / 1000)) : null
}

export function formatDuration(seconds) {
  if (seconds == null) return ''
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
