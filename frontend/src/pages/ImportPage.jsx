import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useActivity } from '../activity'
import { api } from '../api'
import { JobCard } from '../components/Activity'
import { CHART_LABELS } from '../format'

const THIS_YEAR = new Date().getFullYear()
const FIRST_YEAR = 1958 // the Billboard Hot 100's first year

export default function ImportPage() {
  const { activity, refresh } = useActivity()
  const [charts, setCharts] = useState(['billboard_hot_100_number_ones'])
  const [yearFrom, setYearFrom] = useState(THIS_YEAR - 1)
  const [yearTo, setYearTo] = useState(THIS_YEAR - 1)
  const [error, setError] = useState(null)

  const from = Number(yearFrom)
  const to = Number(yearTo)
  const validRange = from >= FIRST_YEAR && to <= THIS_YEAR && from <= to
  const pages = validRange ? charts.length * (to - from + 1) : 0

  const importJobs = (activity?.active ?? []).filter((j) => j.kind === 'import')
  const lastImport = activity?.recent?.find((j) => j.kind === 'import')

  const toggleChart = (name) =>
    setCharts((selected) => (selected.includes(name) ? selected.filter((c) => c !== name) : [...selected, name]))

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      await api.importWikipediaSweep(charts, from, to)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <>
      <section className="hero">
        <div>
          <h1 className="hero-title">Import charts</h1>
          <p className="hero-sub">
            Pull chart listings from Wikipedia across a range of years. Tracks you already have are matched, not
            duplicated.
          </p>
        </div>

        <form className="stack" onSubmit={submit}>
          <p className="info-label">Charts</p>
          <div className="checkbox-list">
            {Object.entries(CHART_LABELS).map(([name, label]) => (
              <label key={name} className="checkbox">
                <input type="checkbox" checked={charts.includes(name)} onChange={() => toggleChart(name)} />
                {label}
              </label>
            ))}
          </div>

          <p className="info-label">Years</p>
          <div className="year-range">
            <input
              className="input"
              type="number"
              min={FIRST_YEAR}
              max={THIS_YEAR}
              value={yearFrom}
              onChange={(e) => setYearFrom(e.target.value)}
              aria-label="From year"
            />
            <span className="muted">to</span>
            <input
              className="input"
              type="number"
              min={FIRST_YEAR}
              max={THIS_YEAR}
              value={yearTo}
              onChange={(e) => setYearTo(e.target.value)}
              aria-label="To year"
            />
          </div>

          <p className="note">
            {!validRange
              ? `Pick years between ${FIRST_YEAR} and ${THIS_YEAR}, start before end.`
              : charts.length === 0
                ? 'Pick at least one chart.'
                : `${pages} page${pages === 1 ? '' : 's'} to fetch — about ${Math.max(1, Math.round((pages * 2) / 60))} min.`}
          </p>

          <button className="button" type="submit" disabled={!pages || importJobs.length > 0}>
            {importJobs.length > 0 ? 'Importing…' : 'Import'}
          </button>
          {error && <p className="error-banner">{error}</p>}
        </form>
      </section>

      {(importJobs.length > 0 || lastImport) && (
        <section className="tracks-section">
          <p className="info-label">{importJobs.length > 0 ? 'Importing' : 'Last import'}</p>
          {importJobs.map((job) => (
            <JobCard key={job.job_id} job={job} />
          ))}
          {importJobs.length === 0 && lastImport && (
            <>
              <JobCard job={lastImport} />
              <p className="note">
                <Link to="/">View tracks</Link> or <Link to="/admin">fetch their keys</Link>.
              </p>
            </>
          )}
        </section>
      )}
    </>
  )
}
