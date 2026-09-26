"""RQ job functions.

Each job runs in its own worker process, so it opens its own Mongo connection -
`DatabaseManager`/`TrackManager` aren't shared with the Flask process. The search
throttle *is* shared though: its state lives in Redis, so every job paces against
the same cooldown.

Long-running jobs publish a live progress snapshot to `job.meta["progress"]`, which
the API exposes to the frontend (see /api/enrichment/activity).
"""

import asyncio
import os
from typing import Optional

from bson import ObjectId
from redis import Redis
from rq import get_current_job

from services.database_manager import DatabaseManager
from services.search_throttle import SearchThrottle
from services.track_manager import TrackManager


def _track_manager() -> TrackManager:
    redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    return TrackManager(DatabaseManager(), throttle=SearchThrottle(redis_conn))


def _progress_reporter():
    """Publishes progress snapshots onto the running RQ job, if there is one."""
    job = get_current_job()
    if job is None:
        return None

    def report(snapshot: dict) -> None:
        job.meta["progress"] = snapshot
        job.save_meta()

    return report


def run_enrichment(limit: int = 50, max_results: int = 5, search_engines: Optional[str] = None) -> dict:
    """Fetch missing key/BPM data via SearXNG for pending tracks. See TrackManager.enrich_pending."""
    return asyncio.run(
        _track_manager().enrich_pending(
            limit=limit,
            max_results=max_results,
            search_engines=search_engines,
            progress=_progress_reporter(),
        )
    )


def run_enrich_track(track_id: str, max_results: int = 5) -> dict:
    """Fetch key/BPM data via SearXNG for a single track. See TrackManager.enrich_track."""
    outcome = asyncio.run(
        _track_manager().enrich_track(ObjectId(track_id), max_results=max_results, progress=_progress_reporter())
    )
    return {"track_id": track_id, **outcome}


def run_wikipedia_sweep(list_names: list[str], year_from: int, year_to: int) -> dict:
    """Import several Wikipedia charts across a year range. See TrackManager.import_wikipedia_sweep."""
    return _track_manager().import_wikipedia_sweep(list_names, year_from, year_to, progress=_progress_reporter())


def run_recompute(track_id: Optional[str] = None) -> dict:
    """Re-run extraction on cached search results, no network calls. See TrackManager.recompute_from_cache."""
    return _track_manager().recompute_from_cache(ObjectId(track_id) if track_id else None)
