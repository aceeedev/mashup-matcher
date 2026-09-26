"""Pure, side-effect-free helpers for matching tracks by Camelot key and BPM.

These are shared by the SearXNG provider (per-source consensus) and TrackManager
(cross-provider consensus and the compatibility query), so the folding/matching
rules only live in one place.
"""

import re
import unicodedata
from collections import Counter
from typing import Optional, Union

from models.track import CamelotKey

_FOLD_LOW, _FOLD_HIGH = 70.0, 140.0

# ---------------------------------------------------------------------------
# Camelot wheel lookup tables, shared by the SearXNG extractor (parsing key
# names out of snippets) and TrackManager (naming a track's consensus key).
# ---------------------------------------------------------------------------
KEY_NAME_TO_CAMELOT: dict[str, str] = {
    # Major (B)
    "c major": "8B",  "c# major": "3B",  "db major": "3B",
    "d major": "10B", "d# major": "5B",  "eb major": "5B",
    "e major": "12B",
    "f major": "7B",  "f# major": "2B",  "gb major": "2B",
    "g major": "9B",  "g# major": "4B",  "ab major": "4B",
    "a major": "11B", "a# major": "6B",  "bb major": "6B",
    "b major": "1B",
    # Minor (A)
    "c minor": "5A",  "c# minor": "12A", "db minor": "12A",
    "d minor": "7A",  "d# minor": "2A",  "eb minor": "2A",
    "e minor": "9A",
    "f minor": "4A",  "f# minor": "11A", "gb minor": "11A",
    "g minor": "6A",  "g# minor": "1A",  "ab minor": "1A",
    "a minor": "8A",  "a# minor": "3A",  "bb minor": "3A",
    "b minor": "10A",
}

CAMELOT_TO_KEY_NAME: dict[str, str] = {
    "1A": "Ab Minor", "1B": "B Major",
    "2A": "Eb Minor", "2B": "F# Major",
    "3A": "Bb Minor", "3B": "Db Major",
    "4A": "F Minor",  "4B": "Ab Major",
    "5A": "C Minor",  "5B": "Eb Major",
    "6A": "G Minor",  "6B": "Bb Major",
    "7A": "D Minor",  "7B": "F Major",
    "8A": "A Minor",  "8B": "C Major",
    "9A": "E Minor",  "9B": "G Major",
    "10A": "B Minor", "10B": "D Major",
    "11A": "F# Minor", "11B": "A Major",
    "12A": "C# Minor", "12B": "E Major",
}

# "feat. X", "ft. X", "featuring X" - with or without surrounding parens/brackets, to end of string
_FEATURE_PATTERN = re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring)\b.*?[\)\]]\s*|\s+(?:feat\.?|ft\.?|featuring)\b.*$",
    re.IGNORECASE,
)

# Splits a credit line into individual artists. Deliberately does NOT split on
# bare " x " (it breaks names like "Lil Nas X"), only on featuring/&/,/and.
_ARTIST_SPLIT_PATTERN = re.compile(
    r"\s*(?:feat\.?|ft\.?|featuring|&|,|\band\b)\s*", re.IGNORECASE
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold_bpm(bpm: float) -> float:
    """Fold a BPM into [70, 140) so half/double-time readings (e.g. 87 vs 174) compare equal."""
    while bpm >= _FOLD_HIGH:
        bpm /= 2
    while bpm < _FOLD_LOW:
        bpm *= 2
    return bpm


def consensus_bpm(votes: list[tuple[float, int]]) -> Optional[float]:
    """Pick the BPM with the most weighted votes, treating half/double-time readings as the same tempo.

    Returns the most-voted raw BPM from within the winning tempo group, or None if votes is empty.
    """
    if not votes:
        return None

    groups: Counter[int] = Counter()
    for bpm, weight in votes:
        groups[round(fold_bpm(bpm))] += weight
    best_group = groups.most_common(1)[0][0]

    raw: Counter[float] = Counter()
    for bpm, weight in votes:
        if round(fold_bpm(bpm)) == best_group:
            raw[bpm] += weight
    return raw.most_common(1)[0][0]


def bpm_windows(folded: float, tolerance_pct: float = 6.0) -> list[tuple[float, float]]:
    """Windows of folded-BPM values considered close to `folded`, within `tolerance_pct`.

    Returns one or two (lo, hi) ranges: the direct window, plus a second range
    across the 70/140 fold boundary when the direct window would cross it
    (e.g. a folded value near 139 is also close to one near 70, since 139 is
    close to 69.5 - half of it).
    """
    delta = folded * tolerance_pct / 100
    lo, hi = folded - delta, folded + delta
    windows = [(lo, hi)]

    if hi > _FOLD_HIGH:
        windows.append((_FOLD_LOW, hi / 2))
    if lo < _FOLD_LOW:
        windows.append((lo * 2, _FOLD_HIGH))

    return windows


def compatible_camelot_keys(code: Union[str, "CamelotKey"], energy_boost: bool = False) -> set[str]:
    """Camelot codes considered mixable with `code`: itself, its neighbours, and its relative major/minor.

    With `energy_boost`, also includes the +2 "energy boost" position.
    """
    code = str(code.value) if isinstance(code, CamelotKey) else str(code)
    number, letter = int(code[:-1]), code[-1].upper()

    def wrap(n: int) -> int:
        return ((n - 1) % 12) + 1

    other_letter = "B" if letter == "A" else "A"
    keys = {code, f"{wrap(number + 1)}{letter}", f"{wrap(number - 1)}{letter}", f"{number}{other_letter}"}
    if energy_boost:
        keys.add(f"{wrap(number + 2)}{letter}")
    return keys


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _strip_features(text: str) -> str:
    return _FEATURE_PATTERN.sub("", text).strip()


def _normalise_part(text: str) -> str:
    text = _strip_accents(text).casefold()
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def split_artists(artist: str) -> list[str]:
    """Split a credit line into individual artist names, e.g. 'A feat. B & C' -> ['A', 'B', 'C']."""
    parts = [p.strip() for p in _ARTIST_SPLIT_PATTERN.split(artist) if p.strip()]
    return parts or [artist.strip()]


def normalise_track_key(title: str, artist: str) -> str:
    """Build the deduplication key for a track: normalised primary artist + normalised title.

    Case, accents, punctuation, and "feat. ..." credits are ignored so the same
    recording found under slightly different metadata maps to one document.
    Does NOT strip "remix"/"live" (different recordings) or split on " x ".
    """
    primary_artist = split_artists(artist)[0]
    title = _strip_features(title)
    return f"{_normalise_part(primary_artist)}::{_normalise_part(title)}"
