import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useActivity } from '../activity'
import { api } from '../api'
import { STATUS_LABELS, formatBpm, formatKey } from '../format'

function AddTrackForm() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [artist, setArtist] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const track = await api.addTrack(title.trim(), artist.trim())
      navigate(`/tracks/${track._id}`)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <form className="stack" onSubmit={submit}>
      <p className="info-label">Add a track</p>
      <input className="input" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <input className="input" placeholder="Artist" value={artist} onChange={(e) => setArtist(e.target.value)} />
      <button className="button" type="submit" disabled={busy || !title.trim() || !artist.trim()}>
        {busy ? 'Adding…' : 'Add track'}
      </button>
      {error && <p className="error-banner">{error}</p>}
    </form>
  )
}

export default function TracksPage() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [tracks, setTracks] = useState(null)
  const [error, setError] = useState(null)
  // Refetch as enrichment moves tracks between statuses, so the list updates live
  const { activity } = useActivity()
  const countsKey = JSON.stringify(activity?.counts ?? null)

  useEffect(() => {
    // Small debounce so typing in the search box doesn't fire a request per keystroke
    const timer = setTimeout(() => {
      api
        .listTracks({ q, status, limit: 200 })
        .then((data) => {
          setTracks(data)
          setError(null)
        })
        .catch((err) => setError(err.message))
    }, 250)
    return () => clearTimeout(timer)
  }, [q, status, countsKey])

  return (
    <>
      <section className="hero">
        <div>
          <h1 className="hero-title">Match your next mashup</h1>
          <p className="hero-sub">
            Tracks are compared by Camelot key and BPM to find what mixes well together.
          </p>
        </div>
        <AddTrackForm />
      </section>

      <section className="tracks-section">
        <div className="toolbar">
          <input
            className="input"
            placeholder="Search title or artist"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select className="input select" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        {error && <p className="error-banner">{error}</p>}
        {tracks === null && !error && <p className="empty-state">Loading…</p>}
        {tracks?.length === 0 && (
          <p className="empty-state">
            No tracks {q || status ? 'match those filters' : 'yet'}. Add one above, or{' '}
            <Link to="/import">import a chart</Link>.
          </p>
        )}

        {tracks?.map((t) => (
          <Link className="track-row track-link" to={`/tracks/${t._id}`} key={t._id}>
            <span>
              <span className="track-title">{t.title}</span>
              <span className="track-artist"> — {t.artist}</span>
            </span>
            <span className="track-meta">
              <span>{formatKey(t.consensus)}</span>
              <span>{formatBpm(t.consensus)}</span>
              <span className="track-status">{STATUS_LABELS[t.enrichment.status]}</span>
            </span>
          </Link>
        ))}
      </section>
    </>
  )
}
