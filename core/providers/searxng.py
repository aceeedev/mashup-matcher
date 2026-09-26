import asyncio
import logging
import os
import re
from collections import Counter
from collections.abc import Sequence
from typing import Optional
from urllib.parse import urlparse

import dotenv
import httpx
from pydantic import BaseModel, Field

from models.track import AudioFeatures, CamelotKey

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Camelot wheel lookup: normalised "note mode" -> Camelot position
# ---------------------------------------------------------------------------
_KEY_TO_CAMELOT: dict[str, str] = {
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

_CAMELOT_TO_KEY: dict[str, str] = {
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

# Non-ASCII characters are written as escapes so the file survives being re-saved with the wrong encoding
_ACCIDENTALS = "#♯b♭"     # #, ♯, b, ♭
_SEPARATORS = "/|•·-"      # /, |, •, ·, -
_ACCIDENTAL_MAP = {"♯": "#", "♭": "b"}

_MIN_BPM, _MAX_BPM = 40, 240

# Matches e.g. "C# minor", "F♯ Major", "Bb minor", "C♯m", "Abmaj", "Gbm", "key of C♯m".
# The root note is case-sensitive so everyday words like "am" or "cm" aren't read as keys.
_KEY_PATTERN = re.compile(
    rf"\b([A-G])([{_ACCIDENTALS}])?\s*((?i:major|minor|maj|min)|m)(?![A-Za-z])"
)

# Matches Camelot codes e.g. "12A", "Camelot: 12A", "Camelot key 11A", "Key: 5B".
# Labelled codes are weighted higher than bare ones.
_CAMELOT_PATTERN = re.compile(
    r"(?P<label>\b(?i:camelot(?:\s+key)?|key)\s*[:=]?\s*)?"
    r"\b(?P<code>(?:1[0-2]|[1-9])[AB])\b"
)

# Mixgraph compact format: BPM immediately followed by the Camelot code, e.g. "10012A"
_COMPACT_PATTERN = re.compile(r"\b(\d{3,5})([AB])\b")

_CAMELOT_TOKEN = r"(?:1[0-2]|[1-9])[AB]"
_NOTE_TOKEN = rf"[A-G][{_ACCIDENTALS}]?(?:major|minor|maj|min|m)?"
_SEP_TOKEN = rf"\s*[{_SEPARATORS}]\s*"
_BPM_TOKEN = r"(\d{2,3}(?:\.\d+)?)"

# Specialized BPM patterns covering standard labels, AudioKeychain tables, and Mixgraph formats
_BPM_PATTERNS = [
    # 1. Explicit BPM label: 'BPM: 96', 'BPM 96', 'tempo: 96', 'tempo of 96'
    re.compile(rf"\b(?:bpm|tempo)\s*[:=]?\s*(?:of\s+)?{_BPM_TOKEN}\b", re.IGNORECASE),
    # 2. Number followed by bpm: '96 bpm', '96.0 BPM', '96 beats per minute'
    re.compile(rf"\b{_BPM_TOKEN}\s*(?:bpm|beats\s+per\s+minute)\b", re.IGNORECASE),
    # 3. AudioKeychain database rows: '11A / Gbm · 96.00' or 'Gbm / 11A · 96.00'
    re.compile(
        rf"(?:{_CAMELOT_TOKEN}{_SEP_TOKEN}{_NOTE_TOKEN}|{_NOTE_TOKEN}{_SEP_TOKEN}{_CAMELOT_TOKEN})"
        rf"{_SEP_TOKEN}{_BPM_TOKEN}\b",
        re.IGNORECASE,
    ),
    # 4. Mixgraph spaced format: '100 12A' (the compact '10012A' form is handled by _COMPACT_PATTERN)
    re.compile(rf"\b(\d{{2,3}})\s+{_CAMELOT_TOKEN}\b"),
]


def _is_plausible_bpm(bpm: float) -> bool:
    return _MIN_BPM <= bpm <= _MAX_BPM


def _fold_bpm(bpm: float) -> int:
    """Fold a BPM into the 70-140 range so half/double-time readings (e.g. 87 vs 174) compare equal."""
    while bpm >= 140:
        bpm /= 2
    while bpm < 70:
        bpm *= 2
    return round(bpm)


def _split_compact(digits: str, letter: str) -> Optional[tuple[float, str]]:
    """Split a compact Mixgraph token like '10012' + 'A' into (100.0, '12A').

    Two-digit Camelot numbers are tried first, and the split is only accepted
    when both halves are valid.
    """
    for cam_len in (2, 1):
        bpm_part, cam_part = digits[:-cam_len], digits[-cam_len:]
        code = f"{cam_part}{letter}"
        if code in _CAMELOT_TO_KEY and len(bpm_part) >= 2 and _is_plausible_bpm(float(bpm_part)):
            return float(bpm_part), code
    return None


class KeySource(BaseModel):
    """A single source's key and BPM finding."""
    url: str = Field(description="The URL of the search result")
    musical_key: Optional[str] = Field(default=None, description="The musical key found in this result's snippet")
    camelot_key: Optional[CamelotKey] = Field(default=None, description="The corresponding Camelot key")
    bpm: Optional[float] = Field(default=None, description="The BPM found in this result's snippet")


class KeySearchResult(BaseModel):
    """Camelot key and BPM extraction result with per-source breakdown."""
    song_name: str = Field(description="The name of the song")
    artist: str = Field(description="The artist of the song")
    musical_key: Optional[str] = Field(default=None, description="The musical key for the consensus Camelot key")
    camelot_key: Optional[CamelotKey] = Field(default=None, description="The consensus Camelot key across all sources")
    bpm: Optional[float] = Field(default=None, description="The consensus BPM across all sources")
    sources: list[KeySource] = Field(
        default_factory=list,
        description="Per-site findings used to determine the result",
    )

    def to_audio_features(self, source: str = "searxng") -> Optional[AudioFeatures]:
        """Convert to an AudioFeatures record, or None if either the key or BPM is missing."""
        if self.bpm is None or self.camelot_key is None:
            return None
        return AudioFeatures(bpm=self.bpm, key=self.camelot_key, source=source)


class SearXNGProvider:
    """Provider for searching Camelot keys and BPM via SearXNG snippet extraction."""

    def __init__(
        self,
        searxng_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 10.0,
    ):
        """Initialize the provider with SearXNG connection settings.

        Falls back to the SEARXNG_URL environment variable if not provided,
        defaulting to http://localhost:8080.

        Args:
            searxng_url: Base URL of the local SearXNG instance.
            client: Optional shared httpx.AsyncClient. Pass one when running many
                    searches so connections are reused; otherwise a client is
                    created per search.
            timeout: Request timeout in seconds.
        """
        self.searxng_url = (searxng_url or os.getenv("SEARXNG_URL", "http://localhost:8080")).rstrip("/")
        self.timeout = timeout
        self._client = client

    async def _get(self, client: httpx.AsyncClient, params: dict) -> httpx.Response:
        return await client.get(
            f"{self.searxng_url}/search",
            params=params,
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )

    @staticmethod
    def _is_excluded(url: str, excluded: Sequence[str]) -> bool:
        """Check whether the URL's host is one of the excluded domains or a subdomain of one."""
        host = (urlparse(url).hostname or "").lower()
        return any(host == domain or host.endswith(f".{domain}") for domain in excluded)

    async def _search_searxng(
        self,
        query: str,
        engines: Optional[str] = None,
        max_results: int = 5,
        exclude_domains: Optional[Sequence[str]] = None,
    ) -> list[dict]:
        """Query SearXNG and return the top results with their URLs and snippets.

        Args:
            query: The search query string.
            engines: Comma-separated list of engines to restrict the search to.
                     Pass None to use all configured engines.
            max_results: Maximum number of results to return.
            exclude_domains: Optional list of domains to filter out (subdomains included).

        Returns:
            A list of dicts with 'url' and 'content' (title + snippet) keys. Empty if
            there were no results or SearXNG rate limited the request.

        Raises:
            PermissionError: If SearXNG returns 403 (JSON format not enabled).
            httpx.HTTPStatusError: If the SearXNG request fails for any other reason.
        """
        params = {"q": query, "format": "json"}
        if engines:
            params["engines"] = engines

        if self._client is not None:
            response = await self._get(self._client, params)
        else:
            async with httpx.AsyncClient() as client:
                response = await self._get(client, params)

        if response.status_code == 429:
            logger.warning("SearXNG rate limited the query %r", query)
            return []
        if response.status_code == 403:
            raise PermissionError(
                f"SearXNG returned 403 Forbidden for {self.searxng_url}. "
                "Ensure JSON format is enabled in your SearXNG settings.yml:\n"
                "  search:\n"
                "    formats:\n"
                "      - html\n"
                "      - json  # <-- add this line"
            )
        response.raise_for_status()
        data = response.json()

        if unresponsive := data.get("unresponsive_engines"):
            logger.warning("SearXNG engines unresponsive for %r: %s", query, unresponsive)

        excluded = [d.lower() for d in exclude_domains or ()]
        filtered_results = []

        for r in data.get("results", []):
            url = r.get("url", "")
            if self._is_excluded(url, excluded):
                continue
            filtered_results.append(
                {
                    "url": url,
                    "content": " | ".join(filter(None, [r.get("title", ""), r.get("content", "")])),
                }
            )
            if len(filtered_results) >= max_results:
                break

        return filtered_results

    @staticmethod
    def _normalise_key(root: str, accidental: Optional[str], mode: str) -> str:
        """Normalise regex match groups into a _KEY_TO_CAMELOT lookup string."""
        note = root.upper() + _ACCIDENTAL_MAP.get(accidental, accidental or "")
        mode_norm = "major" if mode.lower().startswith("maj") else "minor"
        return f"{note} {mode_norm}".lower()

    @classmethod
    def _extract_camelot_from_text(cls, text: str) -> Optional[str]:
        """Extract the most prominent Camelot code from text, from either Camelot notation or key names."""
        weights: Counter[str] = Counter()

        # 1. Explicit Camelot codes (e.g. 'Camelot: 12A', '11A / Gbm')
        for match in _CAMELOT_PATTERN.finditer(text):
            weights[match.group("code")] += 3 if match.group("label") else 2

        # 2. Mixgraph compact codes (e.g. '10012A')
        for match in _COMPACT_PATTERN.finditer(text):
            split = _split_compact(match.group(1), match.group(2))
            if split:
                weights[split[1]] += 2

        # 3. Musical key names (e.g. 'C# Minor', 'key of C♯m', 'Gb minor')
        for match in _KEY_PATTERN.finditer(text):
            code = _KEY_TO_CAMELOT.get(cls._normalise_key(*match.groups()))
            if code:
                weights[code] += 1

        if not weights:
            return None
        return weights.most_common(1)[0][0]

    @staticmethod
    def _extract_bpm_from_text(text: str) -> Optional[float]:
        """Extract the most plausible BPM from text using multi-format patterns."""
        # Keyed by match position so a number caught by several patterns
        # (e.g. 'Tempo: 128 BPM') is only counted once
        found: dict[int, float] = {}
        for pattern in _BPM_PATTERNS:
            for match in pattern.finditer(text):
                found.setdefault(match.start(1), float(match.group(1)))

        for match in _COMPACT_PATTERN.finditer(text):
            split = _split_compact(match.group(1), match.group(2))
            if split:
                found.setdefault(match.start(1), split[0])

        candidates = [round(bpm, 1) for bpm in found.values() if _is_plausible_bpm(bpm)]
        if not candidates:
            return None
        return Counter(candidates).most_common(1)[0][0]

    @staticmethod
    def _consensus_bpm(votes: list[tuple[float, int]]) -> Optional[float]:
        """Pick the BPM with the most weighted votes, treating half/double-time readings as the same tempo.

        Returns the most-voted raw BPM from within the winning tempo group.
        """
        if not votes:
            return None

        groups: Counter[int] = Counter()
        for bpm, weight in votes:
            groups[_fold_bpm(bpm)] += weight
        best_group = groups.most_common(1)[0][0]

        raw: Counter[float] = Counter()
        for bpm, weight in votes:
            if _fold_bpm(bpm) == best_group:
                raw[bpm] += weight
        return raw.most_common(1)[0][0]

    async def get_camelot_key(
        self,
        song_name: str,
        artist: str,
        search_engines: Optional[str] = None,
        max_results: int = 5,
        exclude_domains: Optional[Sequence[str]] = ("tunebat.com", "reddit.com"),
    ) -> Optional[KeySearchResult]:
        """Search for and extract the Camelot key and BPM for a given song and artist.

        Queries SearXNG, extracts the Camelot key and BPM from each result snippet
        individually, then returns the weighted consensus along with the full
        per-source breakdown. Results that mention the song name count double.

        Args:
            song_name: The name of the song.
            artist: The name of the artist.
            search_engines: Comma-separated SearXNG engines to use.
                            Defaults to None (uses all configured active engines in SearXNG).
            max_results: Number of search result snippets to scan.
            exclude_domains: Domains to exclude from search results.
                             Defaults to ("tunebat.com", "reddit.com").

        Returns:
            A KeySearchResult with sources, or None if neither key nor BPM could be found
            or if SearXNG rate limited the request.

        Raises:
            PermissionError: If SearXNG returns 403 (misconfiguration).
            httpx.HTTPStatusError: If the SearXNG request fails with a non-rate-limit error.
        """
        query = f"{song_name} {artist} camelot key bpm"
        results = await self._search_searxng(
            query,
            engines=search_engines,
            max_results=max_results,
            exclude_domains=exclude_domains,
        )

        sources: list[KeySource] = []
        key_votes: Counter[str] = Counter()
        bpm_votes: list[tuple[float, int]] = []
        song_folded = song_name.casefold()

        for r in results:
            content = r.get("content", "")
            if not content:
                continue

            camelot_key = self._extract_camelot_from_text(content)
            bpm = self._extract_bpm_from_text(content)
            if camelot_key is None and bpm is None:
                continue

            # Snippets that don't mention the song are often "similar songs" lists for other tracks
            weight = 2 if song_folded in content.casefold() else 1
            if camelot_key:
                key_votes[camelot_key] += weight
            if bpm is not None:
                bpm_votes.append((bpm, weight))

            sources.append(
                KeySource(
                    url=r["url"],
                    musical_key=_CAMELOT_TO_KEY.get(camelot_key) if camelot_key else None,
                    camelot_key=camelot_key,
                    bpm=bpm,
                )
            )

        if not sources:
            return None

        consensus_camelot = key_votes.most_common(1)[0][0] if key_votes else None

        return KeySearchResult(
            song_name=song_name,
            artist=artist,
            musical_key=_CAMELOT_TO_KEY.get(consensus_camelot) if consensus_camelot else None,
            camelot_key=consensus_camelot,
            bpm=self._consensus_bpm(bpm_votes),
            sources=sources,
        )


if __name__ == "__main__":
    # Run from the core/ directory with: python -m providers.searxng
    async def main():
        logging.basicConfig(level=logging.INFO)
        provider = SearXNGProvider()

        result = await provider.get_camelot_key(
            "Self Aware",
            "Temper City",
            search_engines=None,
            max_results=7,
            exclude_domains=["tunebat.com", "reddit.com"],
        )

        if result:
            print(result.model_dump_json(indent=2))
        else:
            print("Extraction failed.")

    asyncio.run(main())
