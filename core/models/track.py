from collections import Counter
from enum import Enum
import statistics
from typing import List, Optional

from pydantic import BaseModel, Field, computed_field


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


class TrackMetadata(BaseModel):
    title: str
    artist: str
    source: str


class AudioFeatures(BaseModel):
    bpm: float
    key: CamelotKey
    source: str


class AggregatedAudioFeatures(BaseModel):
    """Aggregated summary of audio features across multiple sources."""

    average_bpm: Optional[float] = None
    median_bpm: Optional[float] = None
    key: Optional[CamelotKey] = None
    sources: List[str] = Field(default_factory=list)


class TrackData(BaseModel):
    track_metadata: List[TrackMetadata] = Field(default_factory=list)
    audio_features: List[AudioFeatures] = Field(default_factory=list)

    @computed_field
    @property
    def overall_audio_features(self) -> Optional[AggregatedAudioFeatures]:
        """Calculates overall/aggregated audio features across all sources."""
        if not self.audio_features:
            return None

        bpms = [af.bpm for af in self.audio_features]
        keys = [af.key for af in self.audio_features]
        sources = [af.source for af in self.audio_features]

        avg_bpm = round(statistics.mean(bpms), 2)
        med_bpm = round(statistics.median(bpms), 2)
        # Most frequent CamelotKey (mode)
        mode_key = Counter(keys).most_common(1)[0][0]

        return AggregatedAudioFeatures(
            average_bpm=avg_bpm,
            median_bpm=med_bpm,
            key=mode_key,
            sources=sources,
        )

    @property
    def average_bpm(self) -> Optional[float]:
        """Convenience property for average BPM."""
        return self.overall_audio_features.average_bpm if self.overall_audio_features else None

    @property
    def median_bpm(self) -> Optional[float]:
        """Convenience property for median BPM."""
        return self.overall_audio_features.median_bpm if self.overall_audio_features else None

    @property
    def overall_key(self) -> Optional[CamelotKey]:
        """Convenience property for the consensus / most frequent Camelot key."""
        return self.overall_audio_features.key if self.overall_audio_features else None

    @property
    def primary_metadata(self) -> Optional[TrackMetadata]:
        """Returns the primary (first available) metadata record."""
        return self.track_metadata[0] if self.track_metadata else None

    @property
    def title(self) -> Optional[str]:
        """Convenience property for track title."""
        return self.primary_metadata.title if self.primary_metadata else None

    @property
    def artist(self) -> Optional[str]:
        """Convenience property for artist name."""
        return self.primary_metadata.artist if self.primary_metadata else None

    def add_metadata(self, metadata: TrackMetadata) -> None:
        """Add a new metadata record from a source."""
        self.track_metadata.append(metadata)

    def add_audio_features(self, features: AudioFeatures) -> None:
        """Add a new audio feature record from a source."""
        self.audio_features.append(features)
