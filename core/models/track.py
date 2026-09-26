from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Optional

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

# Validates a Mongo ObjectId (or its string form coming back from JSON) on the way in.
# Only stringifies on JSON serialisation (model_dump(mode="json")/model_dump_json());
# plain model_dump() keeps a real ObjectId so it round-trips through pymongo correctly.
PyObjectId = Annotated[
    ObjectId,
    BeforeValidator(lambda v: v if isinstance(v, ObjectId) else ObjectId(v)),
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CamelotKey(str, Enum):
    A1 = "1A"
    A2 = "2A"
    A3 = "3A"
    A4 = "4A"
    A5 = "5A"
    A6 = "6A"
    A7 = "7A"
    A8 = "8A"
    A9 = "9A"
    A10 = "10A"
    A11 = "11A"
    A12 = "12A"
    B1 = "1B"
    B2 = "2B"
    B3 = "3B"
    B4 = "4B"
    B5 = "5B"
    B6 = "6B"
    B7 = "7B"
    B8 = "8B"
    B9 = "9B"
    B10 = "10B"
    B11 = "11B"
    B12 = "12B"


class DiscoverySource(BaseModel):
    """Where a track was discovered from, e.g. a Wikipedia chart list, or a manual add."""

    source: str
    list: Optional[str] = None
    year: Optional[int] = None


class ExternalIds(BaseModel):
    """Cross-references to other providers, filled in as they're looked up."""

    musicbrainz_recording_id: Optional[str] = None
    theaudiodb_track_id: Optional[str] = None


class Reading(BaseModel):
    """A single source's key/BPM finding for a track. Both fields are optional
    since a source (e.g. one SearXNG search result) may only report one of them."""

    provider: str
    url: Optional[str] = None
    bpm: Optional[float] = None
    camelot_key: Optional[CamelotKey] = None
    fetched_at: datetime = Field(default_factory=_utcnow)


class Consensus(BaseModel):
    """The current best-guess key/BPM for a track, derived from `Track.readings`.

    Stored (rather than computed on read) so the matching query can index on it.
    """

    camelot_key: Optional[CamelotKey] = None
    camelot_number: Optional[int] = None
    camelot_mode: Optional[Literal["A", "B"]] = None
    musical_key: Optional[str] = None
    bpm: Optional[float] = None
    bpm_folded: Optional[float] = None
    key_agreement: Optional[float] = None
    bpm_agreement: Optional[float] = None
    source_count: int = 0
    computed_at: Optional[datetime] = None


class Enrichment(BaseModel):
    """Work-queue state for fetching key/BPM data for this track."""

    status: Literal["pending", "done", "not_found", "failed"] = "pending"
    attempts: int = 0
    last_attempt_at: Optional[datetime] = None
    next_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None


class Track(BaseModel):
    """One uniquely-identified song. `track_key` is the dedup key (see utils.music.normalise_track_key)."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    track_key: str
    title: str
    artist: str
    artists: list[str] = Field(default_factory=list)
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    discovered_from: list[DiscoverySource] = Field(default_factory=list)
    readings: list[Reading] = Field(default_factory=list)
    consensus: Optional[Consensus] = None
    enrichment: Enrichment = Field(default_factory=Enrichment)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SearchCacheEntry(BaseModel):
    """Raw provider search results for a track, kept so extraction can be improved
    and re-run later without re-querying (and re-rate-limiting) the provider."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    track_id: PyObjectId
    provider: str
    query: str
    results: list[dict[str, Any]]
    fetched_at: datetime = Field(default_factory=_utcnow)


class MashupIdea(BaseModel):
    """A saved pairing of two tracks the user is considering mashing up."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    pair_key: str
    track_ids: list[PyObjectId]
    score: Optional[float] = None
    notes: str = ""
    status: Literal["idea", "tried", "made"] = "idea"
    created_at: datetime = Field(default_factory=_utcnow)
