import { useEffect, useState } from 'react'

// The browser calls this directly, so it must be a browser-reachable URL
// (the published port), not the internal Docker service name.
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

function App() {
  const [health, setHealth] = useState(null)
  const [tracks, setTracks] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API_URL}/api/health`)
      .then((res) => res.json())
      .then(setHealth)
      .catch((err) => setError(err.message))

    fetch(`${API_URL}/api/tracks`)
      .then((res) => res.json())
      .then(setTracks)
      .catch((err) => setError(err.message))
  }, [])

  const online = health?.mongo && health?.redis

  return (
    <div className="page">
      <div className="card">
        <header className="header">
          <span className="brand">Mashup Matcher</span>
          <span className="status-pill">
            <span className={`status-dot ${health ? (online ? 'ok' : 'err') : ''}`} />
            {health ? (online ? 'All systems online' : 'Degraded') : 'Checking…'}
          </span>
        </header>

        <section className="hero">
          <div>
            <h1 className="hero-title">Match your next mashup</h1>
            <p className="hero-sub">
              Tracks are compared by Camelot key and BPM to find what mixes well together.
            </p>
            {error && <p className="error-banner">Error reaching the API: {error}</p>}
          </div>

          <div className="info-grid">
            <div>
              <p className="info-label">Database</p>
              <p className="info-value">
                Mongo {health ? (health.mongo ? '✅' : '❌') : <span className="muted">…</span>}
              </p>
              <p className="info-value">
                Redis {health ? (health.redis ? '✅' : '❌') : <span className="muted">…</span>}
              </p>
            </div>
            <div>
              <p className="info-label">Tracks</p>
              <p className="info-value">{tracks ? tracks.length : <span className="muted">…</span>}</p>
            </div>
          </div>
        </section>

        <section className="tracks-section">
          <p className="info-label">Recent tracks</p>

          {tracks && tracks.length === 0 && <p className="empty-state">No tracks yet.</p>}

          {tracks && tracks.length > 0 && (
            <div>
              {tracks.map((t) => (
                <div className="track-row" key={t._id}>
                  <span>
                    <span className="track-title">{t.title}</span>
                    {' — '}
                    <span className="track-artist">{t.artist}</span>
                  </span>
                  <span className="track-status">{t.enrichment.status}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <footer className="footer">
          API: <code>{API_URL}</code>
        </footer>
      </div>
    </div>
  )
}

export default App
