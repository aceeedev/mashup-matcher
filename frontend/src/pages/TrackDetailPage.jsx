import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useActivity } from '../activity'
import { api } from '../api'
import { Countdown, JobResult, JobStateLine } from '../components/Activity'
import { STATUS_LABELS, formatBpm, formatKey, formatPercent } from '../format'

function hostname(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function Matches({ track }) {
  const [tolerance, setTolerance] = useState(6)
  const [energyBoost, setEnergyBoost] = useState(false)
  const [matches, setMatches] = useState(null)
  const [saved, setSaved] = useState({})
  const [error, setError] = useState(null)

  const matchable = track.consensus?.camelot_key && track.consensus?.bpm_folded != null

  useEffect(() => {
    if (!matchable) return
    setMatches(null)
    setError(null)
    api
      .getMatches(track._id, { bpm_tolerance_pct: tolerance, energy_boost: energyBoost, limit: 50 })
      .then(setMatches)
      .catch((err) => setError(err.message))
  }, [track, matchable, tolerance, energyBoost])

  const saveIdea = async (match) => {
    const otherId = match.track._id
    setSaved((s) => ({ ...s, [otherId]: 'saving' }))
    try {
      await api.saveIdea(track._id, otherId, match.score)
      setSaved((s) => ({ ...s, [otherId]: 'saved' }))
    } catch (err) {
      setSaved((s) => ({ ...s, [otherId]: undefined }))
      setError(err.message)
    }
  }

  return (
    <section className="tracks-section">
      <div className="section-head">
        <p className="info-label">Compatible tracks</p>
        {matchable && (
          <div className="toolbar compact">
            <select
              className="input select"
              value={tolerance}
              onChange={(e) => setTolerance(Number(e.target.value))}
            >
              <option value={3}>± 3% BPM</option>
              <option value={6}>± 6% BPM</option>
              <option value={10}>± 10% BPM</option>
            </select>
            <label className="checkbox">
              <input type="checkbox" checked={energyBoost} onChange={(e) => setEnergyBoost(e.target.checked)} />
              Energy boost
            </label>
          </div>
        )}
      </div>

      {!matchable && (
        <p className="empty-state">Fetch this track's key and BPM first to find matches.</p>
      )}
      {error && <p className="error-banner">{error}</p>}
      {matchable && matches === null && !error && <p className="empty-state">Finding matches…</p>}
      {matches?.length === 0 && <p className="empty-state">No compatible tracks yet. Add or import more tracks.</p>}

      {matches?.map((m) => (
        <div className="track-row" key={m.track._id}>
          <span>
            <Link className="track-title" to={`/tracks/${m.track._id}`}>
              {m.track.title}
            </Link>
            <span className="track-artist"> — {m.track.artist}</span>
          </span>
          <span className="track-meta">
            <span>{formatKey(m.track.consensus)}</span>
            <span>{formatBpm(m.track.consensus)}</span>
            <span className="score">{formatPercent(m.score)}</span>
            <button
              className="button secondary small"
              disabled={Boolean(saved[m.track._id])}
              onClick={() => saveIdea(m)}
            >
              {saved[m.track._id] === 'saved' ? 'Saved' : saved[m.track._id] === 'saving' ? 'Saving…' : 'Save idea'}
            </button>
          </span>
        </div>
      ))}
    </section>
  )
}

export default function TrackDetailPage() {
  const { id } = useParams()
  const { activity, refresh } = useActivity()
  const [track, setTrack] = useState(null)
  const [error, setError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [startedJobId, setStartedJobId] = useState(null)
  const [recomputeMessage, setRecomputeMessage] = useState(null)
  const [recomputing, setRecomputing] = useState(false)

  // Any fetch touching this track: our own single-track job, or a bulk run currently on it
  const active = activity?.active ?? []
  const myJob = active.find((j) => j.kind === 'track' && j.track?._id === id)
  const bulkJob = active.find((j) => j.kind === 'bulk' && j.progress?.current?.track_id === id)
  const lastJob = startedJobId && !myJob ? activity?.recent?.find((j) => j.job_id === startedJobId) : null
  // Just clicked, but the next poll hasn't shown the job yet
  const awaitingJob = Boolean(startedJobId && !myJob && !lastJob)
  const fetching = Boolean(myJob || bulkJob || awaitingJob)

  useEffect(() => {
    setStartedJobId(null)
    setRecomputeMessage(null)
  }, [id])

  useEffect(() => {
    api
      .getTrack(id)
      .then((data) => {
        setTrack(data)
        setError(null)
      })
      .catch((err) => setError(err.message))
  }, [id, reloadKey])

  // Pick up the new readings/consensus as soon as a fetch for this track finishes
  const wasFetching = useRef(false)
  useEffect(() => {
    if (wasFetching.current && !fetching) setReloadKey((k) => k + 1)
    wasFetching.current = fetching
  }, [fetching])

  const startEnrich = async () => {
    setRecomputeMessage(null)
    try {
      const { job_id } = await api.enrichTrack(id)
      setStartedJobId(job_id)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  const recompute = async () => {
    setRecomputing(true)
    setRecomputeMessage(null)
    try {
      const result = await api.recomputeTrack(id)
      setRecomputeMessage(
        result.processed ? 'Re-extracted from cached search results.' : 'No cached search results yet — fetch first.',
      )
      setReloadKey((k) => k + 1)
    } catch (err) {
      setError(err.message)
    } finally {
      setRecomputing(false)
    }
  }

  if (error && !track) return <p className="error-banner">{error}</p>
  if (!track) return <p className="empty-state">Loading…</p>

  const c = track.consensus
  const throttle = activity?.throttle

  return (
    <>
      <Link to="/" className="back-link">
        ← All tracks
      </Link>

      <section className="hero">
        <div>
          <h1 className="hero-title">{track.title}</h1>
          <p className="hero-sub">{track.artist}</p>

          <div className="actions">
            <button className="button" onClick={startEnrich} disabled={fetching}>
              {fetching ? 'Fetching…' : c ? 'Refresh key & BPM' : 'Fetch key & BPM'}
            </button>
            <button className="button secondary" onClick={recompute} disabled={recomputing || fetching}>
              {recomputing ? 'Re-extracting…' : 'Re-extract from cache'}
            </button>
          </div>

          <div className="live-status">
            {myJob && <JobStateLine job={myJob} />}
            {!myJob && bulkJob && (
              <>
                <p className="job-reason">Being fetched by the bulk enrichment run</p>
                <JobStateLine job={bulkJob} />
              </>
            )}
            {awaitingJob && <p className="job-state">Queuing…</p>}
            {lastJob && <JobResult job={lastJob} />}
            {!fetching && throttle?.blocked && (
              <p className="job-state">
                <span className="status-dot err inline" />
                Search engines are cooling down — a fetch now waits <Countdown epoch={throttle.blocked_until} />
              </p>
            )}
            {recomputeMessage && <p className="note">{recomputeMessage}</p>}
            {error && <p className="error-banner">{error}</p>}
          </div>
        </div>

        <div className="info-grid">
          <div>
            <p className="info-label">Key</p>
            <p className="info-value">{formatKey(c)}</p>
          </div>
          <div>
            <p className="info-label">Tempo</p>
            <p className="info-value">{formatBpm(c)}</p>
          </div>
          <div>
            <p className="info-label">Status</p>
            <p className="info-value">{STATUS_LABELS[track.enrichment.status]}</p>
          </div>
          <div>
            <p className="info-label">Agreement</p>
            <p className="info-value">
              {c ? (
                <>
                  {formatPercent(c.key_agreement)} key <span className="muted">·</span> {formatPercent(c.bpm_agreement)}{' '}
                  BPM
                </>
              ) : (
                '—'
              )}
            </p>
          </div>
        </div>
      </section>

      <Matches track={track} />

      {track.readings.length > 0 && (
        <section className="tracks-section">
          <p className="info-label">Sources ({track.readings.length})</p>
          {track.readings.map((r, i) => (
            <div className="track-row" key={`${r.url}-${i}`}>
              <a className="track-artist" href={r.url} target="_blank" rel="noreferrer">
                {hostname(r.url)}
              </a>
              <span className="track-meta">
                <span>{r.camelot_key || '—'}</span>
                <span>{r.bpm != null ? `${r.bpm} BPM` : '—'}</span>
              </span>
            </div>
          ))}
        </section>
      )}
    </>
  )
}
