import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { formatBpm, formatKey, formatPercent } from '../format'

const IDEA_STATUSES = { idea: 'Idea', tried: 'Tried', made: 'Made' }

function IdeaRow({ idea, onChange, onDelete }) {
  const [notes, setNotes] = useState(idea.notes || '')
  const [a, b] = idea.tracks

  const saveNotes = () => {
    if (notes !== (idea.notes || '')) onChange(idea._id, { notes })
  }

  return (
    <div className="idea-row">
      <div className="idea-pair">
        {[a, b].map((t) => (
          <div key={t._id}>
            {t.title ? (
              <Link className="track-title" to={`/tracks/${t._id}`}>
                {t.title}
              </Link>
            ) : (
              <span className="track-artist">Deleted track</span>
            )}
            {t.artist && <span className="track-artist"> — {t.artist}</span>}
            <div className="track-meta">
              <span>{formatKey(t.consensus)}</span>
              <span>{formatBpm(t.consensus)}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="idea-controls">
        <span className="score">{formatPercent(idea.score)}</span>
        <select
          className="input select"
          value={idea.status}
          onChange={(e) => onChange(idea._id, { status: e.target.value })}
        >
          {Object.entries(IDEA_STATUSES).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <input
          className="input"
          placeholder="Notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={saveNotes}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        />
        <button className="button secondary small" onClick={() => onDelete(idea._id)}>
          Delete
        </button>
      </div>
    </div>
  )
}

export default function IdeasPage() {
  const [status, setStatus] = useState('')
  const [ideas, setIdeas] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setIdeas(null)
    api
      .listIdeas(status)
      .then((data) => {
        setIdeas(data)
        setError(null)
      })
      .catch((err) => setError(err.message))
  }, [status])

  const update = async (id, changes) => {
    try {
      const updated = await api.updateIdea(id, changes)
      setIdeas((list) =>
        list
          .map((i) => (i._id === id ? updated : i))
          // Drop it from view if it no longer matches the active status filter
          .filter((i) => !status || i.status === status),
      )
    } catch (err) {
      setError(err.message)
    }
  }

  const remove = async (id) => {
    try {
      await api.deleteIdea(id)
      setIdeas((list) => list.filter((i) => i._id !== id))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <>
      <section className="hero">
        <div>
          <h1 className="hero-title">Mashup ideas</h1>
          <p className="hero-sub">Pairs you've saved from a track's compatible matches.</p>
        </div>
      </section>

      <section className="tracks-section">
        <div className="tabs">
          {[['', 'All'], ...Object.entries(IDEA_STATUSES)].map(([value, label]) => (
            <button
              key={value}
              className={`tab ${status === value ? 'active' : ''}`}
              onClick={() => setStatus(value)}
            >
              {label}
            </button>
          ))}
        </div>

        {error && <p className="error-banner">{error}</p>}
        {ideas === null && !error && <p className="empty-state">Loading…</p>}
        {ideas?.length === 0 && (
          <p className="empty-state">
            No ideas {status ? `marked “${IDEA_STATUSES[status]}”` : 'yet'}. Save one from a track's matches.
          </p>
        )}

        {ideas?.map((idea) => (
          <IdeaRow key={idea._id} idea={idea} onChange={update} onDelete={remove} />
        ))}
      </section>
    </>
  )
}
