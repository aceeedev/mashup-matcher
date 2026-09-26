import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import ActivityProvider from './ActivityProvider'
import { api } from './api'
import { ActivityIndicator } from './components/Activity'
import AdminPage from './pages/AdminPage'
import IdeasPage from './pages/IdeasPage'
import ImportPage from './pages/ImportPage'
import TrackDetailPage from './pages/TrackDetailPage'
import TracksPage from './pages/TracksPage'

function StatusPill() {
  const [health, setHealth] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    api.health().then(setHealth).catch(() => setFailed(true))
  }, [])

  const online = health?.mongo && health?.redis
  const state = failed ? 'err' : health ? (online ? 'ok' : 'err') : ''
  const label = failed ? 'API unreachable' : health ? (online ? 'All systems online' : 'Degraded') : 'Checking…'

  // While enrichment is running or the search engines are blocked, show that instead
  return (
    <Link to="/admin" className="status-pill">
      <ActivityIndicator
        fallback={
          <>
            <span className={`status-dot ${state}`} />
            {label}
          </>
        }
      />
    </Link>
  )
}

function App() {
  return (
    <ActivityProvider>
      <div className="page">
        <div className="card">
          <header className="header">
            <Link to="/" className="brand">
              Mashup Matcher
            </Link>
            <nav className="nav">
              <NavLink to="/" end className="nav-link">
                Tracks
              </NavLink>
              <NavLink to="/ideas" className="nav-link">
                Ideas
              </NavLink>
              <NavLink to="/import" className="nav-link">
                Import
              </NavLink>
              <NavLink to="/admin" className="nav-link">
                Enrichment
              </NavLink>
            </nav>
            <StatusPill />
          </header>

          <Routes>
            <Route path="/" element={<TracksPage />} />
            <Route path="/tracks/:id" element={<TrackDetailPage />} />
            <Route path="/ideas" element={<IdeasPage />} />
            <Route path="/import" element={<ImportPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="*" element={<p className="empty-state">Page not found.</p>} />
          </Routes>
        </div>
      </div>
    </ActivityProvider>
  )
}

export default App
