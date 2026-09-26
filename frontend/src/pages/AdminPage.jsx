import { useState } from 'react'
import { useActivity } from '../activity'
import { api } from '../api'
import { EngineStatus, JobCard } from '../components/Activity'
import { STATUS_LABELS } from '../format'

export default function AdminPage() {
  const { activity, error: activityError, refresh } = useActivity()
  const [limit, setLimit] = useState(20)
  const [error, setError] = useState(null)

  const active = activity?.active ?? []
  const recent = activity?.recent ?? []
  const bulkActive = active.some((j) => j.kind === 'bulk')
  const recomputeActive = active.some((j) => j.kind === 'recompute')
  const throttle = activity?.throttle

  const startRun = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      await api.runEnrichment({ limit: Number(limit) })
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const startRecompute = async () => {
    setError(null)
    try {
      await api.recomputeAll()
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <>
      <section className="hero">
        <div>
          <h1 className="hero-title">Enrichment</h1>
          <p className="hero-sub">Fetch key and BPM data for pending tracks in the background.</p>
          {(error || activityError) && <p className="error-banner">{error || activityError}</p>}
        </div>

        <div className="info-grid">
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <div key={value}>
              <p className="info-label">{label}</p>
              <p className="info-value big">{activity ? (activity.counts[value] ?? 0) : '…'}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="tracks-section">
        <p className="info-label">Live activity</p>
        <EngineStatus />
        {activity && active.length === 0 && <p className="empty-state">Nothing running right now.</p>}
        {active.map((job) => (
          <JobCard key={job.job_id} job={job} />
        ))}
      </section>

      <section className="tracks-section two-col">
        <form className="stack" onSubmit={startRun}>
          <p className="info-label">Fetch pending tracks</p>
          <p className="hero-sub">
            Searches run one at a time
            {throttle ? `, ${throttle.min_delay}–${throttle.max_delay}s apart,` : ''} and pause automatically when
            search engines start blocking requests. Failed tracks are retried once their backoff has passed.
          </p>
          <label className="field">
            <span>Tracks per run</span>
            <input
              className="input"
              type="number"
              min="1"
              max="500"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
            />
          </label>
          <button className="button" type="submit" disabled={bulkActive || !limit}>
            {bulkActive ? 'Running…' : 'Start'}
          </button>
        </form>

        <div className="stack">
          <p className="info-label">Re-extract everything</p>
          <p className="hero-sub">
            Re-runs key/BPM extraction on every cached search result. No new searches are made.
          </p>
          <button className="button secondary" onClick={startRecompute} disabled={recomputeActive}>
            {recomputeActive ? 'Running…' : 'Re-extract all'}
          </button>
        </div>
      </section>

      {recent.length > 0 && (
        <section className="tracks-section">
          <p className="info-label">Recent</p>
          {recent.map((job) => (
            <JobCard key={job.job_id} job={job} />
          ))}
        </section>
      )}
    </>
  )
}
