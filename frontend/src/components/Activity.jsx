import { useState } from 'react'
import { formatDuration, toLocalMs, useActivity, useCountdown } from '../activity'
import { api } from '../api'
import { CHART_LABELS } from '../format'

export function Countdown({ epoch }) {
  const { activity } = useActivity()
  const seconds = useCountdown(toLocalMs(activity, epoch))
  return <span className="countdown">{formatDuration(seconds)}</span>
}

/** Whether searches are currently paced normally or paused because engines blocked us. */
export function EngineStatus() {
  const { activity } = useActivity()
  const throttle = activity?.throttle
  if (!throttle) return null

  if (throttle.blocked) {
    return (
      <div className="engine-status">
        <span className="status-dot err" />
        <div>
          <p className="job-state">
            Search engines are blocking requests · all searches paused for <Countdown epoch={throttle.blocked_until} />
          </p>
          {throttle.reason && <p className="job-reason">{throttle.reason}</p>}
        </div>
      </div>
    )
  }

  return (
    <div className="engine-status">
      <span className="status-dot ok" />
      <p className="job-state">
        Search engines ready · one search at a time, {throttle.min_delay}–{throttle.max_delay}s apart
      </p>
    </div>
  )
}

function jobTitle(job) {
  const total = job.progress?.total ?? job.limit
  if (job.kind === 'bulk') return total != null ? `Fetching ${total} pending track${total === 1 ? '' : 's'}` : 'Fetching pending tracks'
  if (job.kind === 'track') return `Fetching “${job.track?.title ?? 'track'}”`
  if (job.kind === 'recompute') return 'Re-extracting cached results'
  if (job.kind === 'import') {
    const { list_names: lists = [], year_from: from, year_to: to } = job.params || {}
    const years = from === to ? `${from}` : `${from}–${to}`
    return `Importing ${lists.length} chart${lists.length === 1 ? '' : 's'} · ${years}`
  }
  return job.kind
}

function badge(job) {
  if (job.status === 'queued') return ['Queued', '']
  if (job.status === 'started') return ['Running', 'active']
  if (job.status === 'failed') return ['Failed', 'err']
  if (job.status === 'stopped') return ['Stopped', 'err']
  if (job.status === 'canceled') return ['Canceled', '']
  const r = job.result || {}
  if (r.stopped_early || r.status === 'blocked') return ['Stopped', 'err']
  return ['Done', 'ok']
}

function countsText(counts) {
  if (!counts) return null
  const parts = [
    counts.done && `${counts.done} found`,
    counts.not_found && `${counts.not_found} not found`,
    counts.failed && `${counts.failed} failed`,
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

/** What a running or queued job is doing right now, with live countdowns. */
export function JobStateLine({ job }) {
  const { activity } = useActivity()
  const p = job.progress
  const maxBlocks = activity?.throttle?.max_consecutive_blocks

  if (job.status === 'queued') {
    return (
      <p className="job-state">
        Queued{job.position ? ` · #${job.position} in line` : ''}
        {activity?.throttle?.blocked && <span className="muted"> · will wait for the engine cooldown</span>}
      </p>
    )
  }
  if (!p || p.state === 'starting') {
    return <p className="job-state">{job.kind === 'recompute' ? 'Running…' : 'Starting…'}</p>
  }

  if (job.kind === 'import') {
    return p.current ? (
      <p className="job-state">
        Importing {CHART_LABELS[p.current.list] ?? p.current.list} · {p.current.year}…
      </p>
    ) : null
  }

  const current = p.current ? `“${p.current.title}” — ${p.current.artist}` : ''

  if (p.state === 'searching') return <p className="job-state">Searching {current}…</p>
  if (p.state === 'waiting') {
    return (
      <p className="job-state">
        Next search in <Countdown epoch={p.wait_until} />
        {current && <span className="muted"> · {current}</span>}
      </p>
    )
  }
  if (p.state === 'backing_off') {
    return (
      <>
        <p className="job-state">
          <span className="status-dot err inline" />
          Search engines are blocking requests — retrying in <Countdown epoch={p.wait_until} />
          {p.blocks > 0 && maxBlocks && (
            <span className="muted">
              {' '}
              · block {p.blocks} of {maxBlocks} before stopping
            </span>
          )}
        </p>
        {p.reason && <p className="job-reason">{p.reason}</p>}
      </>
    )
  }
  return null
}

/** The outcome of a finished (or failed) job, in plain words. */
export function JobResult({ job }) {
  if (job.status === 'failed') return <p className="job-state err-text">Failed: {job.error || 'unknown error'}</p>
  if (job.status === 'canceled') return <p className="job-state">Canceled before it started.</p>
  if (job.status === 'stopped') {
    // A stopped job has no result, but its last progress snapshot shows how far it got
    const p = job.progress
    const counts = job.kind === 'import' ? null : countsText(p?.counts)
    return (
      <>
        <p className="job-state">
          Stopped by you
          {p?.total > 1 && ` after ${p.processed} of ${p.total}`}
          {counts && <span className="muted"> · {counts}</span>}
        </p>
        {job.kind === 'import' && <ImportSummary counts={p?.counts} />}
        {job.kind === 'bulk' && <p className="job-reason">Tracks it didn't reach are still pending.</p>}
      </>
    )
  }

  const r = job.result || {}
  if (job.kind === 'track') {
    const text = {
      done: 'Found key and BPM.',
      not_found: 'No key or BPM found in the search results.',
      failed: `Search failed: ${r.reason || 'unknown error'}`,
      blocked: 'Search engines blocked the search — try again once the cooldown ends.',
    }[r.status]
    return (
      <>
        <p className={`job-state ${r.status === 'blocked' || r.status === 'failed' ? 'err-text' : ''}`}>{text}</p>
        {r.status === 'blocked' && r.reason && <p className="job-reason">{r.reason}</p>}
      </>
    )
  }

  if (job.kind === 'import') return <ImportSummary counts={r} final />

  const summary = job.kind === 'bulk' ? `Processed ${r.processed ?? 0} of ${r.total ?? 0}` : `Re-extracted ${r.processed ?? 0}`
  const counts = countsText(r)
  return (
    <>
      <p className="job-state">
        {summary}
        {counts && <span className="muted"> · {counts}</span>}
      </p>
      {r.stopped_early && (
        <p className="job-reason">
          {r.stopped_early}. {r.remaining} left pending for the next run.
        </p>
      )}
    </>
  )
}

function ImportSummary({ counts, final = false }) {
  if (!counts) return null
  const parts = [
    `${counts.rows ?? 0} rows`,
    `${counts.new_tracks ?? 0} new tracks`,
    counts.missing && `${counts.missing} page${counts.missing === 1 ? '' : 's'} not on Wikipedia`,
    counts.failed && `${counts.failed} failed`,
  ].filter(Boolean)
  return (
    <>
      <p className={final ? 'job-state' : 'job-counts'}>{parts.join(' · ')}</p>
      {final && counts.errors?.length > 0 && <p className="job-reason">{counts.errors.slice(0, 3).join(' · ')}</p>}
    </>
  )
}

function StopButton({ job }) {
  const { refresh } = useActivity()
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState(null)

  const stop = async () => {
    setStopping(true)
    setError(null)
    try {
      await api.stopJob(job.job_id)
      refresh()
    } catch (err) {
      setError(err.message)
      setStopping(false)
    }
  }

  return (
    <>
      <button className="button secondary small" onClick={stop} disabled={stopping}>
        {stopping ? 'Stopping…' : job.status === 'queued' ? 'Cancel' : 'Stop'}
      </button>
      {error && <span className="job-reason err-text">{error}</span>}
    </>
  )
}

export function JobCard({ job }) {
  const [label, tone] = badge(job)
  const active = job.status === 'queued' || job.status === 'started'
  const p = job.progress
  const total = p?.total ?? 0
  const processed = active || job.status === 'stopped' ? (p?.processed ?? 0) : (job.result?.processed ?? p?.processed ?? 0)
  const counts = active && job.kind !== 'import' ? countsText(p?.counts) : null
  const showProgress = (job.kind === 'bulk' || job.kind === 'import') && total > 1

  return (
    <div className="job-card">
      <div className="job-head">
        <span className="job-title">{jobTitle(job)}</span>
        <span className="job-head-actions">
          <span className={`job-badge ${tone}`}>{label}</span>
          {active && <StopButton job={job} />}
        </span>
      </div>

      {showProgress && (
        <div className="progress-row">
          <div className="progress">
            <div className="progress-fill" style={{ width: `${Math.round((processed / total) * 100)}%` }} />
          </div>
          <span className="progress-label">
            {processed}/{total}
          </span>
        </div>
      )}

      {active ? <JobStateLine job={job} /> : <JobResult job={job} />}
      {counts && <p className="job-counts">{counts}</p>}
      {active && job.kind === 'import' && <ImportSummary counts={p?.counts} />}
    </div>
  )
}

/** Compact header indicator: blocked engines > a running job > plain system health. */
export function ActivityIndicator({ fallback }) {
  const { activity } = useActivity()
  const throttle = activity?.throttle
  const running = activity?.active?.find((j) => j.status === 'started') ?? activity?.active?.[0]

  if (throttle?.blocked) {
    return (
      <>
        <span className="status-dot err" />
        Engines blocked · <Countdown epoch={throttle.blocked_until} />
      </>
    )
  }
  if (running) {
    const p = running.progress
    const verb = running.kind === 'import' ? 'Importing' : 'Fetching'
    const text =
      running.status === 'queued'
        ? 'Queued'
        : (running.kind === 'bulk' || running.kind === 'import') && p?.total
          ? `${verb} ${p.processed}/${p.total}`
          : `${verb}…`
    return (
      <>
        <span className="status-dot ok pulse" />
        {text}
      </>
    )
  }
  return fallback
}
