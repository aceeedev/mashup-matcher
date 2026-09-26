import asyncio
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from bson import ObjectId
from pymongo import ReturnDocument

from models.track import Consensus, DiscoverySource, MashupIdea, Reading, SearchCacheEntry, Track
from providers.searxng import SearXNGProvider
from providers.wikipedia import WikipediaProvider
from services.database_manager import DatabaseManager
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


class TrackManager:
    """Track storage, deduplication, per-provider readings, and key/BPM-based matching."""

    def __init__(self, db: DatabaseManager):
        self.db = db

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
                title = str(row[title_column]).strip()
                artist = str(row["Artist(s)"]).strip()
                if not title or not artist:
                    continue
                track_ids.append(self.add_track(title, artist, discovered_from=discovered_from))

        return track_ids

    # -- Enriching via SearXNG ----------------------------------------------------

    async def enrich_pending(
        self,
        limit: int = 50,
        concurrency: int = 4,
        max_results: int = 5,
        search_engines: Optional[str] = None,
    ) -> dict[str, int]:
        """Fetch missing key/BPM data via SearXNG for tracks that need it.

        Picks up tracks with `enrichment.status == "pending"`, plus "failed" tracks
        whose backoff window (`enrichment.next_attempt_at`) has passed. Raw search
        results are cached to `search_cache` (see `SearchCacheEntry`) so extraction
        can be re-run later without re-querying SearXNG, which rate-limits hard.
        Runs up to `concurrency` searches at once over one shared HTTP connection.

        Returns counts of how many tracks ended in each status, e.g.
        {"processed": 12, "done": 8, "not_found": 3, "failed": 1}.
        """
        now = _utcnow()
        query = {
            "$or": [
                {"enrichment.status": "pending"},
                {"enrichment.status": "failed", "enrichment.next_attempt_at": {"$lte": now}},
            ]
        }
        docs = list(self.db.tracks.find(query).limit(limit))
        if not docs:
            return {"processed": 0}

        semaphore = asyncio.Semaphore(concurrency)
        counts: Counter[str] = Counter()

        async with httpx.AsyncClient() as client:
            provider = SearXNGProvider(client=client)

            async def process(doc: dict) -> None:
                async with semaphore:
                    status = await self._enrich_one(doc, provider, max_results, search_engines)
                    counts[status] += 1

            await asyncio.gather(*(process(doc) for doc in docs))

        return {"processed": len(docs), **counts}

    async def _enrich_one(
        self,
        doc: dict,
        provider: SearXNGProvider,
        max_results: int,
        search_engines: Optional[str],
    ) -> str:
        """Run one track's SearXNG search + extraction, store the results, and return its new status."""
        track_id, title, artist = doc["_id"], doc["title"], doc["artist"]
        attempts = doc.get("enrichment", {}).get("attempts", 0)

        try:
            results = await provider.search(title, artist, search_engines=search_engines, max_results=max_results)
        except Exception as exc:
            self._mark_enrichment_failed(track_id, attempts, str(exc))
            return "failed"

        self._cache_search_results(track_id, "searxng", provider.build_query(title, artist), results)

        key_result = provider.extract(title, artist, results)
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

        return self._finalize_consensus_status(track_id, consensus)

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

            track = self.get_track(entry["track_id"])
            if track is None:
                continue  # the track was deleted since this was cached

            readings = extractor(track.title, track.artist, entry["results"])
            consensus = self.record_readings(track.id, entry["provider"], readings)
            counts[self._finalize_consensus_status(track.id, consensus)] += 1

        return {"processed": len(cache_docs), **counts}
