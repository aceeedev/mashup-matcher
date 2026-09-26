// The browser calls this directly, so it must be a browser-reachable URL
// (the published port), not the internal Docker service name.
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

async function request(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return null

  const data = await res.json().catch(() => null)
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`)
  return data
}

function query(params = {}) {
  const defined = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  return defined.length ? `?${new URLSearchParams(defined)}` : ''
}

export const api = {
  health: () => request('/api/health'),

  listTracks: (params) => request(`/api/tracks${query(params)}`),
  addTrack: (title, artist) => request('/api/tracks', { method: 'POST', body: { title, artist } }),
  getTrack: (id) => request(`/api/tracks/${id}`),
  getMatches: (id, params) => request(`/api/tracks/${id}/matches${query(params)}`),
  enrichTrack: (id) => request(`/api/tracks/${id}/enrich`, { method: 'POST' }),
  recomputeTrack: (id) => request(`/api/tracks/${id}/recompute`, { method: 'POST' }),

  enrichmentStatus: () => request('/api/enrichment/status'),
  activity: () => request('/api/enrichment/activity'),
  runEnrichment: (options) => request('/api/enrichment/run', { method: 'POST', body: options }),
  recomputeAll: () => request('/api/enrichment/recompute', { method: 'POST' }),
  getJob: (id) => request(`/api/enrichment/jobs/${id}`),
  stopJob: (id) => request(`/api/enrichment/jobs/${id}/stop`, { method: 'POST' }),

  importWikipedia: (year, listName) =>
    request('/api/imports/wikipedia', { method: 'POST', body: { year, list_name: listName } }),
  importWikipediaSweep: (listNames, yearFrom, yearTo) =>
    request('/api/imports/wikipedia/sweep', {
      method: 'POST',
      body: { list_names: listNames, year_from: yearFrom, year_to: yearTo },
    }),

  listIdeas: (status) => request(`/api/mashup-ideas${query({ status })}`),
  saveIdea: (trackIdA, trackIdB, score) =>
    request('/api/mashup-ideas', { method: 'POST', body: { track_id_a: trackIdA, track_id_b: trackIdB, score } }),
  updateIdea: (id, changes) => request(`/api/mashup-ideas/${id}`, { method: 'PATCH', body: changes }),
  deleteIdea: (id) => request(`/api/mashup-ideas/${id}`, { method: 'DELETE' }),
}
