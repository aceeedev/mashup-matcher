import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import httpx
import pandas as pd
import requests
from bson import ObjectId
from pymongo import ReturnDocument

from models.track import Consensus, DiscoverySource, MashupIdea, Reading, SearchCacheEntry, Track
from providers.searxng import SearXNGProvider
from providers.wikipedia import WikipediaProvider
from services.database_manager import DatabaseManager
from services.search_throttle import SearchThrottle
from utils.music import (
    CAMELOT_TO_KEY_NAME,
    bpm_windows,
    compatible_camelot_keys,
    consensus_bpm,
    fold_bpm,
    normalise_track_key,
    split_artists,
)

# Wikipedia chart list name -> (WikipediaProvider method, its title column)
_WIKIPEDIA_LISTS: dict[str, tuple[str, str]] = {
    "billboard_streaming_songs_number_ones": ("get_billboard_streaming_songs_number_ones", "Song"),
    "billboard_hot_100_number_ones": ("get_billboard_hot_100_number_ones", "Song"),
    "billboard_hot_100_top_ten_singles": ("get_billboard_hot_100_top_ten_singles", "Single"),
}

# Backoff before retrying a failed enrichment attempt: 5m, 30m, 2h, 6h, then capped at 24h
_FAILURE_BACKOFF_MINUTES = (5, 30, 120, 360, 1440)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _extract_searxng_readings(title: str, artist: str, results: list[dict]) -> list[Reading]:
    result = SearXNGProvider.extract(title, artist, results)
    return result.to_readings(provider="searxng") if result else []


# provider name (as stored on SearchCacheEntry.provider) -> (title, artist, raw results) -> readings.
# Extend this when a second provider starts caching raw results.
_EXTRACTORS: dict[str, Callable[[str, str, list[dict]], list[Reading]]] = {
    "searxng": _extract_searxng_readings,
}

ProgressCallback = Callable[[dict], None]


class _Outcome(NamedTuple):
    status: str  # "done" | "not_found" | "failed" | "blocked"
    reason: Optional[str] = None


class _EnrichProgress:
    """Builds the live snapshot an enrichment run reports after every state change.

    Snapshot keys: state ("starting" | "waiting" | "backing_off" | "searching" | "finished" |
    "stopped"), total, processed, counts ({status: n}), blocks (consecutive blocked searches),
    current ({track_id, title, artist} or None), wait_until (epoch seconds), reason, updated_at.
    """

    def __init__(self, total: int, callback: Optional[ProgressCallback]):
        self.callback = callback
        self.snapshot: dict = {
            "state": "starting",
            "total": total,
            "processed": 0,
            "counts": {},
            "blocks": 0,
            "current": None,
            "wait_until": None,
            "reason": None,
        }
        self.emit()

    def emit(self, **changes) -> None:
        self.snapshot.update(changes, updated_at=time.time())
        if self.callback:
            self.callback(dict(self.snapshot))

    def for_track(self, doc: dict) -> Callable[..., None]:
        """A reporter for one track's search: report(state, wait_until=None, reason=None)."""
        current = {"track_id": str(doc["_id"]), "title": doc["title"], "artist": doc["artist"]}

        def report(state: str, wait_until: Optional[float] = None, reason: Optional[str] = None) -> None:
            self.emit(state=state, current=current, wait_until=wait_until, reason=reason)

        return report

    def finish(self, stopped_reason: Optional[str] = None) -> None:
        self.emit(
            state="stopped" if stopped_reason else "finished",
            current=None,
            wait_until=None,
            reason=stopped_reason,
        )


class TrackManager:
    """Track storage, deduplication, per-provider readings, and key/BPM-based matching."""

    def __init__(self, db: DatabaseManager, throttle: Optional[SearchThrottle] = None):
        self.db = db
        # Shared pacing/backoff for SearXNG searches. Pass a Redis-backed one so every
        # job and worker respects the same cooldown; the default is in-process only.
        self.throttle = throttle or SearchThrottle()

    # -- Creating and reading tracks -----------------------------------------

    def add_track(
        self,
        title: str,
        artist: str,
        discovered_from: Optional[DiscoverySource] = None,
    ) -> ObjectId:
        """Add a track, or return the existing one if it's already stored.

        Dedup is by `normalise_track_key(title, artist)`, so re-importing the same
        song under slightly different casing/credits doesn't create a duplicate.
        `discovered_from` (if given) is recorded even on an existing track.
        """
        track_key = normalise_track_key(title, artist)
        now = _utcnow()

        update: dict = {
            "$setOnInsert": {
                "track_key": track_key,
                "title": title,
                "artist": artist,
                "artists": split_artists(artist),
                "external_ids": {"musicbrainz_recording_id": None, "theaudiodb_track_id": None},
                "readings": [],
                "consensus": None,
                "enrichment": {
                    "status": "pending",
                    "attempts": 0,
                    "last_attempt_at": None,
                    "next_attempt_at": None,
                    "last_error": None,
                },
                "created_at": now,
                "updated_at": now,
            },
        }
        if discovered_from is not None:
            update["$addToSet"] = {"discovered_from": discovered_from.model_dump()}

        doc = self.db.tracks.find_one_and_update(
            {"track_key": track_key},
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return doc["_id"]

    def get_track(self, track_id: ObjectId) -> Optional[Track]:
        doc = self.db.tracks.find_one({"_id": track_id})
        return Track.model_validate(doc) if doc else None

    def list_tracks(
        self,
        q: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Track]:
        """List tracks, optionally filtered by a case-insensitive title/artist substring and/or enrichment status."""
        query: dict = {}
        if status:
            query["enrichment.status"] = status
        if q:
            pattern = re.escape(q)
            query["$or"] = [{"title": {"$regex": pattern, "$options": "i"}}, {"artist": {"$regex": pattern, "$options": "i"}}]

        docs = self.db.tracks.find(query).skip(offset).limit(limit)
        return [Track.model_validate(doc) for doc in docs]

    def enrichment_status_counts(self) -> dict[str, int]:
        """Count tracks by `enrichment.status`, e.g. {"pending": 3, "done": 40, "not_found": 5, "failed": 1}."""
        pipeline = [{"$group": {"_id": "$enrichment.status", "count": {"$sum": 1}}}]
        return {doc["_id"]: doc["count"] for doc in self.db.tracks.aggregate(pipeline)}

    # -- Storing provider readings and computing consensus -------------------

    def record_readings(self, track_id: ObjectId, provider: str, readings: list[Reading]) -> Optional[Consensus]:
        """Replace a provider's readings on a track with a fresh batch, then recompute consensus.

        Passing an empty list clears that provider's prior readings (e.g. it found nothing this time).
        """
        self.db.tracks.update_one(
            {"_id": track_id},
            {
                "$pull": {"readings": {"provider": provider}},
                "$set": {"updated_at": _utcnow()},
            },
        )
        if readings:
            self.db.tracks.update_one(
                {"_id": track_id},
                {"$push": {"readings": {"$each": [r.model_dump() for r in readings]}}},
            )
        return self.recompute_consensus(track_id)

    def recompute_consensus(self, track_id: ObjectId) -> Optional[Consensus]:
        """Recompute `Track.consensus` from the readings currently stored on the track.

        Also updates `Track.enrichment.status` to "done" (a consensus was found) or
        "not_found" (there are readings but none carry a key or BPM).
        """
        track = self.get_track(track_id)
        if track is None:
            return None

        key_votes: Counter[str] = Counter(r.camelot_key.value for r in track.readings if r.camelot_key)
        bpm_votes: list[tuple[float, int]] = [(r.bpm, 1) for r in track.readings if r.bpm is not None]

        consensus = None
        if key_votes or bpm_votes:
            camelot_key = key_votes.most_common(1)[0][0] if key_votes else None
            bpm = consensus_bpm(bpm_votes)

            consensus = Consensus(
                camelot_key=camelot_key,
                camelot_number=int(camelot_key[:-1]) if camelot_key else None,
                camelot_mode=camelot_key[-1] if camelot_key else None,
                musical_key=CAMELOT_TO_KEY_NAME.get(camelot_key) if camelot_key else None,
                bpm=bpm,
                bpm_folded=fold_bpm(bpm) if bpm is not None else None,
                key_agreement=(key_votes[camelot_key] / sum(key_votes.values())) if key_votes else None,
                bpm_agreement=(
                    sum(w for b, w in bpm_votes if round(fold_bpm(b)) == round(fold_bpm(bpm))) / len(bpm_votes)
                    if bpm_votes and bpm is not None
                    else None
                ),
                source_count=len(track.readings),
                computed_at=_utcnow(),
            )

        status = "done" if consensus else ("not_found" if track.readings else "pending")
        self.db.tracks.update_one(
            {"_id": track_id},
            {
                "$set": {
                    "consensus": consensus.model_dump() if consensus else None,
                    "enrichment.status": status,
                    "updated_at": _utcnow(),
                }
            },
        )
        return consensus

    # -- Matching -------------------------------------------------------------

    def find_compatible(
        self,
        track_id: ObjectId,
        bpm_tolerance_pct: float = 6.0,
        energy_boost: bool = False,
        limit: int = 25,
    ) -> list[tuple[Track, float]]:
        """Find tracks that would mix well with `track_id`, ranked highest score first.

        Requires the source track to have a consensus key and BPM. Compatibility is
        computed at query time (see utils.music) rather than precomputed, so it never
        goes stale when a track's readings change.
        """
        track = self.get_track(track_id)
        if track is None or track.consensus is None:
            return []
        if track.consensus.camelot_key is None or track.consensus.bpm_folded is None:
            return []

        candidate_keys = compatible_camelot_keys(track.consensus.camelot_key, energy_boost=energy_boost)
        bpm_ranges = bpm_windows(track.consensus.bpm_folded, bpm_tolerance_pct)

        query = {
            "_id": {"$ne": track_id},
            "consensus.camelot_key": {"$in": sorted(candidate_keys)},
            "$or": [{"consensus.bpm_folded": {"$gte": lo, "$lte": hi}} for lo, hi in bpm_ranges],
        }

        results: list[tuple[Track, float]] = []
        for doc in self.db.tracks.find(query):
            candidate = Track.model_validate(doc)
            results.append((candidate, self._score(track, candidate)))

        results.sort(key=lambda pair: pair[1], reverse=True)
        return results[:limit]

    @staticmethod
    def _score(a: Track, b: Track) -> float:
        """Score a candidate match: closer BPM and a stronger key relation score higher."""
        ca, cb = a.consensus, b.consensus

        key_a, key_b = str(ca.camelot_key.value), str(cb.camelot_key.value)
        if key_a == key_b:
            key_weight = 1.0
        elif key_a[-1] == key_b[-1]:
            key_weight = 0.8  # adjacent on the wheel (same mode, neighbouring number)
        else:
            key_weight = 0.6  # relative major/minor

        bpm_closeness = 1.0 - min(abs(ca.bpm_folded - cb.bpm_folded) / ca.bpm_folded, 1.0)
        agreement = ((ca.key_agreement or 1.0) + (cb.key_agreement or 1.0)) / 2

        return round(key_weight * bpm_closeness * agreement, 4)

    # -- Saved ideas ------------------------------------------------------------

    def save_idea(self, track_id_a: ObjectId, track_id_b: ObjectId, score: Optional[float] = None, notes: str = "") -> ObjectId:
        """Save a track pairing. Idempotent: saving the same pair again updates it rather than duplicating."""
        if track_id_a == track_id_b:
            raise ValueError("Cannot pair a track with itself.")

        ordered = sorted([track_id_a, track_id_b], key=str)
        pair_key = f"{ordered[0]}:{ordered[1]}"
        new_idea = MashupIdea(pair_key=pair_key, track_ids=ordered)

        doc = self.db.mashup_ideas.find_one_and_update(
            {"pair_key": pair_key},
            {
                "$setOnInsert": new_idea.model_dump(exclude={"id", "score", "notes"}),
                "$set": {"score": score, "notes": notes},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return doc["_id"]

    def list_mashup_ideas(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> list[MashupIdea]:
        query = {"status": status} if status else {}
        docs = self.db.mashup_ideas.find(query).sort("created_at", -1).skip(offset).limit(limit)
        return [MashupIdea.model_validate(doc) for doc in docs]

    def update_mashup_idea(
        self,
        idea_id: ObjectId,
        status: Optional[str] = None,
        notes: Optional[str] = None,
        score: Optional[float] = None,
    ) -> Optional[MashupIdea]:
        """Update whichever of status/notes/score are given. Fields left as None are left unchanged."""
        changes = {k: v for k, v in {"status": status, "notes": notes, "score": score}.items() if v is not None}
        if not changes:
            return self.get_mashup_idea(idea_id)

        doc = self.db.mashup_ideas.find_one_and_update(
            {"_id": idea_id}, {"$set": changes}, return_document=ReturnDocument.AFTER
        )
        return MashupIdea.model_validate(doc) if doc else None

    def get_mashup_idea(self, idea_id: ObjectId) -> Optional[MashupIdea]:
        doc = self.db.mashup_ideas.find_one({"_id": idea_id})
        return MashupIdea.model_validate(doc) if doc else None

    def delete_mashup_idea(self, idea_id: ObjectId) -> bool:
        return self.db.mashup_ideas.delete_one({"_id": idea_id}).deleted_count > 0

    # -- Importing from Wikipedia -----------------------------------------------

    def import_from_wikipedia(self, year: int, list_name: str) -> list[ObjectId]:
        """Import a Wikipedia chart list for a year, adding (or matching) each row as a track.

        `list_name` is one of: {list(_WIKIPEDIA_LISTS)}. Every row is tagged with a
        `DiscoverySource` recording where it came from.
        """
        if list_name not in _WIKIPEDIA_LISTS:
            raise ValueError(f"Unknown Wikipedia list {list_name!r}. Choose from: {sorted(_WIKIPEDIA_LISTS)}")

        method_name, title_column = _WIKIPEDIA_LISTS[list_name]
        provider = WikipediaProvider()
        tables = getattr(provider, method_name)(year, track_metadata_only=True)

        discovered_from = DiscoverySource(source="wikipedia", list=list_name, year=year)
        track_ids: list[ObjectId] = []
        for table in tables:
            for _, row in table.iterrows():
                raw_title, raw_artist = row[title_column], row["Artist(s)"]
                # Blank cells come through as NaN, which str() would turn into a track called "nan"
                if pd.isna(raw_title) or pd.isna(raw_artist):
                    continue
                title, artist = str(raw_title).strip(), str(raw_artist).strip()
                if not title or not artist:
                    continue
                track_ids.append(self.add_track(title, artist, discovered_from=discovered_from))

        return track_ids

    @staticmethod
    def validate_wikipedia_sweep(list_names: list[str], year_from: int, year_to: int) -> None:
        """Raise ValueError if a sweep's charts or year range are invalid."""
        if not list_names:
            raise ValueError("Pick at least one chart.")
        unknown = [name for name in list_names if name not in _WIKIPEDIA_LISTS]
        if unknown:
            raise ValueError(f"Unknown Wikipedia list(s) {unknown}. Choose from: {sorted(_WIKIPEDIA_LISTS)}")
        if year_from > year_to:
            raise ValueError("The start year must be before the end year.")
        if year_from < 1900 or year_to > datetime.now().year:
            raise ValueError(f"Years must be between 1900 and {datetime.now().year}.")

    def import_wikipedia_sweep(
        self,
        list_names: list[str],
        year_from: int,
        year_to: int,
        progress: Optional[ProgressCallback] = None,
        delay: float = 1.0,
    ) -> dict:
        """Import several Wikipedia chart lists across a range of years (inclusive).

        Each (chart, year) page is fetched in turn, `delay` seconds apart to go easy on
        Wikipedia. A page that doesn't exist (e.g. a chart that didn't run that year) is
        counted as "missing" rather than failing the sweep; any other per-page error is
        counted as "failed" and the sweep carries on.

        `progress` (if given) receives snapshots like _EnrichProgress's, with
        state "importing" and current {"list": ..., "year": ...}.

        Returns e.g. {"pages": 60, "imported_pages": 55, "missing": 4, "failed": 1,
        "rows": 812, "new_tracks": 590, "errors": [...]}.
        """
        self.validate_wikipedia_sweep(list_names, year_from, year_to)
        pages = [(year, name) for year in range(year_from, year_to + 1) for name in list_names]
        tracks_before = self.db.tracks.count_documents({})
        counts: Counter[str] = Counter()
        rows = 0
        errors: list[str] = []

        snapshot: dict = {"state": "starting", "total": len(pages), "processed": 0, "counts": {}, "current": None}

        def emit(**changes) -> None:
            snapshot.update(changes, updated_at=time.time())
            if progress:
                progress(dict(snapshot))

        emit()
        for i, (year, name) in enumerate(pages):
            if i:
                time.sleep(delay)
            emit(state="importing", current={"list": name, "year": year})
            try:
                rows += len(self.import_from_wikipedia(year, name))
                counts["imported_pages"] += 1
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    counts["missing"] += 1
                else:
                    counts["failed"] += 1
                    errors.append(f"{name} {year}: {exc}")
            except Exception as exc:
                counts["failed"] += 1
                errors.append(f"{name} {year}: {exc}")

            emit(
                processed=i + 1,
                counts={**counts, "rows": rows, "new_tracks": self.db.tracks.count_documents({}) - tracks_before},
            )

        result = {
            "pages": len(pages),
            **counts,
            "rows": rows,
            "new_tracks": self.db.tracks.count_documents({}) - tracks_before,
            "errors": errors[:20],
        }
        emit(state="finished", current=None)
        return result

    # -- Enriching via SearXNG ----------------------------------------------------

    async def enrich_pending(
        self,
        limit: int = 50,
        max_results: int = 5,
        search_engines: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> dict:
        """Fetch missing key/BPM data via SearXNG for tracks that need it.

        Picks up tracks with `enrichment.status == "pending"`, plus "failed" tracks
        whose backoff window (`enrichment.next_attempt_at`) has passed. Searches run
        one at a time, paced and backed off by `self.throttle` - SearXNG's engines
        rate-limit by IP, so parallel searches only get them blocked sooner.

        When a search comes back blocked, the same track is retried once the throttle's
        cooldown passes; after `throttle.max_consecutive_blocks` blocks in a row the run
        stops early and the remaining tracks stay pending for the next run.

        `progress` (if given) is called with a snapshot dict after every state change
        (see `_EnrichProgress`), so callers can show the run live.

        Returns counts, e.g. {"total": 20, "processed": 12, "done": 8, "not_found": 3,
        "failed": 1, "stopped_early": "...", "remaining": 8}.
        """
        now = _utcnow()
        query = {
            "$or": [
                {"enrichment.status": "pending"},
                {"enrichment.status": "failed", "enrichment.next_attempt_at": {"$lte": now}},
            ]
        }
        docs = list(self.db.tracks.find(query).limit(limit))
        tracker = _EnrichProgress(total=len(docs), callback=progress)
        if not docs:
            tracker.finish()
            return {"total": 0, "processed": 0}

        counts: Counter[str] = Counter()
        processed = 0
        consecutive_blocks = 0
        stopped_early: Optional[str] = None

        async with httpx.AsyncClient() as client:
            provider = SearXNGProvider(client=client)
            while processed < len(docs):
                doc = docs[processed]
                outcome = await self._enrich_one(doc, provider, max_results, search_engines, tracker.for_track(doc))

                if outcome.status == "blocked":
                    consecutive_blocks += 1
                    tracker.emit(blocks=consecutive_blocks)
                    if consecutive_blocks >= self.throttle.max_consecutive_blocks:
                        stopped_early = f"Stopped after {consecutive_blocks} blocked searches in a row ({outcome.reason})"
                        break
                    continue  # retry the same track - throttle.wait() holds it until the cooldown passes

                consecutive_blocks = 0
                counts[outcome.status] += 1
                processed += 1
                tracker.emit(processed=processed, counts=dict(counts), blocks=0)

        result = {"total": len(docs), "processed": processed, **counts}
        if stopped_early:
            result.update(stopped_early=stopped_early, remaining=len(docs) - processed)
        tracker.finish(stopped_early)
        return result

    async def enrich_track(
        self,
        track_id: ObjectId,
        max_results: int = 5,
        search_engines: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> dict:
        """Fetch key/BPM data via SearXNG for one track, regardless of its current status.

        Waits for the throttle like any other search. Returns {"status": ..., "reason": ...}
        where status is "done", "not_found", "failed", or "blocked" (the track is left as it was).
        """
        doc = self.db.tracks.find_one({"_id": track_id})
        if doc is None:
            raise ValueError(f"Track {track_id} not found")

        tracker = _EnrichProgress(total=1, callback=progress)
        async with httpx.AsyncClient() as client:
            outcome = await self._enrich_one(
                doc, SearXNGProvider(client=client), max_results, search_engines, tracker.for_track(doc)
            )

        blocked = outcome.status == "blocked"
        if not blocked:
            tracker.emit(processed=1, counts={outcome.status: 1})
        tracker.finish(outcome.reason if blocked else None)
        return {"status": outcome.status, "reason": outcome.reason}

    async def _enrich_one(
        self,
        doc: dict,
        provider: SearXNGProvider,
        max_results: int,
        search_engines: Optional[str],
        report: Optional[Callable[..., None]] = None,
    ) -> "_Outcome":
        """Run one track's throttled SearXNG search + extraction and store the results.

        Returns an _Outcome whose status is "done", "not_found", "failed", or "blocked".
        A blocked search (rate limiting, or SearXNG unreachable) leaves the track untouched:
        it says nothing about the track, so it mustn't count against the track's own retry backoff.
        """
        track_id, title, artist = doc["_id"], doc["title"], doc["artist"]
        attempts = doc.get("enrichment", {}).get("attempts", 0)

        await self.throttle.wait(report)
        if report:
            report("searching")

        try:
            response = await provider.search(title, artist, search_engines=search_engines, max_results=max_results)
        except httpx.TransportError as exc:
            # SearXNG itself is unreachable or timing out - a global problem, not this track's
            reason = f"SearXNG unreachable ({type(exc).__name__})"
            self.throttle.record_block(reason)
            return _Outcome("blocked", reason)
        except PermissionError:
            raise  # SearXNG misconfigured (JSON output disabled) - fail the whole run loudly
        except Exception as exc:
            self._mark_enrichment_failed(track_id, attempts, str(exc))
            return _Outcome("failed", str(exc))

        if response.blocked:
            self.throttle.record_block(response.block_reason)
            return _Outcome("blocked", response.block_reason)
        self.throttle.record_success()

        if response.results:
            # Only cache real results - never overwrite a good cache entry with nothing
            self._cache_search_results(track_id, "searxng", provider.build_query(title, artist), response.results)

        key_result = provider.extract(title, artist, response.results)
        readings = key_result.to_readings(provider="searxng") if key_result else []
        consensus = self.record_readings(track_id, "searxng", readings)

        self.db.tracks.update_one(
            {"_id": track_id},
            {
                "$set": {
                    "enrichment.attempts": attempts + 1,
                    "enrichment.last_attempt_at": _utcnow(),
                    "enrichment.next_attempt_at": None,
                    "enrichment.last_error": None,
                }
            },
        )

        return _Outcome(self._finalize_consensus_status(track_id, consensus))

    def _finalize_consensus_status(self, track_id: ObjectId, consensus: Optional[Consensus]) -> str:
        """After a genuine extraction attempt (live search or cache reprocessing), override the
        ambiguous "pending" status `recompute_consensus` falls back to when there are no readings
        at all - since we know an attempt was actually made, "no consensus" means a confirmed miss.
        """
        if consensus is None:
            self.db.tracks.update_one({"_id": track_id}, {"$set": {"enrichment.status": "not_found"}})
            return "not_found"
        return "done"

    def _mark_enrichment_failed(self, track_id: ObjectId, attempts: int, error: str) -> None:
        attempts += 1
        delay_minutes = _FAILURE_BACKOFF_MINUTES[min(attempts - 1, len(_FAILURE_BACKOFF_MINUTES) - 1)]
        self.db.tracks.update_one(
            {"_id": track_id},
            {
                "$set": {
                    "enrichment.status": "failed",
                    "enrichment.attempts": attempts,
                    "enrichment.last_attempt_at": _utcnow(),
                    "enrichment.next_attempt_at": _utcnow() + timedelta(minutes=delay_minutes),
                    "enrichment.last_error": error,
                }
            },
        )

    def _cache_search_results(self, track_id: ObjectId, provider: str, query: str, results: list[dict]) -> None:
        entry = SearchCacheEntry(track_id=track_id, provider=provider, query=query, results=results)
        self.db.search_cache.update_one(
            {"track_id": track_id, "provider": provider},
            {"$set": entry.model_dump(exclude={"id"})},
            upsert=True,
        )

    # -- Recomputing from cache (no network) -------------------------------------

    def recompute_from_cache(self, track_id: Optional[ObjectId] = None) -> dict[str, int]:
        """Re-run extraction on cached provider results, without any network calls.

        Useful after improving an extractor's regexes: reprocesses the raw snippets
        already in `search_cache` instead of re-querying (and re-rate-limiting) the
        provider. Pass `track_id` to limit this to one track; omit it to reprocess
        every cached entry.

        Returns counts of how many entries ended in each status, e.g.
        {"processed": 5, "done": 4, "not_found": 1}.
        """
        query = {"track_id": track_id} if track_id is not None else {}
        cache_docs = list(self.db.search_cache.find(query))
        if not cache_docs:
            return {"processed": 0}

        counts: Counter[str] = Counter()
        for entry in cache_docs:
            extractor = _EXTRACTORS.get(entry["provider"])
            if extractor is None:
                continue  # no extractor registered for this provider - shouldn't normally happen
            if not entry["results"]:
                continue  # an empty search (e.g. rate-limited) isn't evidence the data doesn't exist

            track = self.get_track(entry["track_id"])
            if track is None:
                continue  # the track was deleted since this was cached

            readings = extractor(track.title, track.artist, entry["results"])
            consensus = self.record_readings(track.id, entry["provider"], readings)
            counts[self._finalize_consensus_status(track.id, consensus)] += 1

        return {"processed": len(cache_docs), **counts}
