"""RQ job functions.

Each job runs in its own worker process, so it opens its own Mongo connection -
`DatabaseManager`/`TrackManager` aren't shared with the Flask process.
"""

import asyncio
from typing import Optional

from bson import ObjectId

from services.database_manager import DatabaseManager
from services.track_manager import TrackManager


def _track_manager() -> TrackManager:
    return TrackManager(DatabaseManager())


def run_enrichment(
    limit: int = 50,
    concurrency: int = 4,
    max_results: int = 5,
    search_engines: Optional[str] = None,
) -> dict:
    """Fetch missing key/BPM data via SearXNG for pending tracks. See TrackManager.enrich_pending."""
    return asyncio.run(
        _track_manager().enrich_pending(
            limit=limit,
            concurrency=concurrency,
            max_results=max_results,
            search_engines=search_engines,
        )
    )


def run_recompute(track_id: Optional[str] = None) -> dict:
    """Re-run extraction on cached search results, no network calls. See TrackManager.recompute_from_cache."""
    return _track_manager().recompute_from_cache(ObjectId(track_id) if track_id else None)
