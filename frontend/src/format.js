export const STATUS_LABELS = {
  pending: 'Pending',
  done: 'Done',
  not_found: 'Not found',
  failed: 'Failed',
}

// Must match the list names TrackManager.import_from_wikipedia accepts
export const CHART_LABELS = {
  billboard_hot_100_number_ones: 'Billboard Hot 100 number ones',
  billboard_hot_100_top_ten_singles: 'Billboard Hot 100 top-ten singles',
  billboard_streaming_songs_number_ones: 'Billboard Streaming Songs number ones',
}

export function formatKey(consensus) {
  if (!consensus?.camelot_key) return '—'
  return consensus.musical_key ? `${consensus.camelot_key} · ${consensus.musical_key}` : consensus.camelot_key
}

export function formatBpm(consensus) {
  if (consensus?.bpm == null) return '—'
  return `${Math.round(consensus.bpm * 10) / 10} BPM`
}

export function formatPercent(value) {
  return value == null ? '—' : `${Math.round(value * 100)}%`
}
