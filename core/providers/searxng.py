import asyncio
import os
import re
from typing import Optional

import dotenv
import httpx
from pydantic import BaseModel, Field

dotenv.load_dotenv()

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

# Matches e.g. "C# minor", "Fâ™¯ Major", "Bb minor", "Câ™¯m", "Abmaj", "Gbm", "key of Câ™¯m"
_KEY_PATTERN = re.compile(
    r'\b([A-G])'                        # root note
    r'([#â™¯bâ™­])?'                        # optional accidental
    r'\s*'
    r'(major|minor|maj|min|m(?![a-z]))',  # mode
    re.IGNORECASE,
)

# Matches explicit Camelot codes e.g. "Camelot: 12A", "Camelot 11A", "Key: 12A", "11A / Gbm", "100 12A"
_CAMELOT_PATTERN = re.compile(
    r'(?:camelot(?:[:\s]+key)?[:\s]+|key[:\s]+|\b)(1[0-2]|[1-9])([AB])\b',
    re.IGNORECASE,
)

# Specialized BPM patterns covering standard labels, AudioKeychain tables, and Mixgraph formats
_BPM_PATTERNS = [
    # 1. Explicit BPM label: 'BPM: 96', 'BPM 96', 'tempo: 96', 'tempo of 96'
    re.compile(r'\b(?:bpm|tempo)\s*[:=]?\s*(?:of\s+)?(\d{2,3}(?:\.\d+)?)\b', re.IGNORECASE),
    # 2. Number followed by bpm: '96 bpm', '96.0 BPM', '96 beats per minute'
    re.compile(r'\b(\d{2,3}(?:\.\d+)?)\s*(?:bpm\b|beats\s+per\s+minute\b)', re.IGNORECASE),
    # 3. AudioKeychain database rows: '11A / Gbm Â· 96.00' or 'Gbm / 11A Â· 96.00'
    re.compile(r'(?:1[0-2]|[1-9])[AB]\s*[/|â€¢Â·-]\s*[A-Ga-g][#â™¯bâ™­]?(?:m|min|maj|minor|major)?\s*[/|â€¢Â·-]\s*(\d{2,3}(?:\.\d+)?)\b', re.IGNORECASE),
    # 4. Mixgraph compact format: '10012A' or '100 12A'
    re.compile(r'\b(\d{2,3})\s*(?:1[0-2]|[1-9])[AB]\b', re.IGNORECASE),
]

_ACCIDENTAL_MAP = {"#": "#", "â™¯": "#", "b": "b", "â™­": "b"}


class KeySource(BaseModel):
    """A single source's key and BPM finding."""
    url: str = Field(description="The URL of the search result")
    musical_key: Optional[str] = Field(default=None, description="The musical key found in this result's snippet")
    camelot_key: Optional[str] = Field(default=None, description="The corresponding Camelot key")
    bpm: Optional[float] = Field(default=None, description="The BPM found in this result's snippet")


class CamelotKey(BaseModel):
    """Camelot key and BPM extraction result with per-source breakdown."""
    song_name: str = Field(description="The name of the song")
    artist: str = Field(description="The artist of the song")
    musical_key: Optional[str] = Field(default=None, description="The most common musical key across all sources")
    camelot_key: Optional[str] = Field(default=None, description="The Camelot key for the most common musical key")
    bpm: Optional[float] = Field(default=None, description="The most common BPM across all sources")
    sources: list[KeySource] = Field(
        default_factory=list,
        description="Per-site findings used to determine the result",
    )


class SearXNGProvider:
    """Provider for searching Camelot keys and BPM via SearXNG snippet extraction."""

    def __init__(
        self,
        searxng_url: Optional[str] = None,
    ):
        """Initialize the provider with SearXNG connection settings.

        Falls back to the SEARXNG_URL environment variable if not provided,
        defaulting to http://localhost:8080.

        Args:
            searxng_url: Base URL of the local SearXNG instance.
        """
        self.searxng_url = (searxng_url or os.getenv("SEARXNG_URL", "http://localhost:8080")).rstrip("/")

    async def _search_searxng(
        self,
        query: str,
        engines: Optional[str] = None,
        max_results: int = 5,
        exclude_domains: Optional[list[str]] = None,
    ) -> list[dict]:
        """Query SearXNG and return the top results with their URLs and snippets.

        Args:
            query: The search query string.
            engines: Comma-separated list of engines to restrict the search to.
                     Pass None to use all configured engines.
            max_results: Maximum number of results to return.
            exclude_domains: Optional list of domains or keywords to filter out.

        Returns:
            A list of dicts with 'url' and 'content' (title + snippet) keys.

        Raises:
            ValueError: If no results are returned.
            PermissionError: If SearXNG returns 403 (JSON format not enabled).
            httpx.HTTPStatusError: If the SearXNG request fails.
        """
        params = {"q": query, "format": "json"}
        if engines:
            params["engines"] = engines

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.searxng_url}/search",
                params=params,
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
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

        results = data.get("results")
        if not results:
            raise ValueError(f"No SearXNG results found for query: {query!r}")

        filtered_results = []
        exclude_list = [d.lower() for d in exclude_domains] if exclude_domains else []

        for r in results:
            url = r.get("url", "")
            if exclude_list and any(domain in url.lower() for domain in exclude_list):
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
        """Normalise regex match groups into a lookup key string."""
        note = root.upper()
        if accidental:
            note += _ACCIDENTAL_MAP.get(accidental, accidental)
        mode_norm = "minor" if mode.lower().startswith("m") and mode.lower() != "maj" else "major"
        if mode.lower() == "maj":
            mode_norm = "major"
        return f"{note} {mode_norm}".lower()

    def _extract_key_from_text(self, text: str) -> Optional[tuple[str, str]]:
        """Extract the most prominent musical key or Camelot code from text and return (musical_key, camelot_key)."""
        counts: dict[str, tuple[str, int]] = {}

        # 1. Look for explicit Camelot codes (e.g. 'Camelot: 12A', '11A / Gbm', '10012A')
        for match in _CAMELOT_PATTERN.finditer(text):
            num, letter = match.group(1), match.group(2).upper()
            cam = f"{num}{letter}"
            if cam in _CAMELOT_TO_KEY:
                musical = _CAMELOT_TO_KEY[cam]
                curr_musical, curr_weight = counts.get(cam, (musical, 0))
                counts[cam] = (curr_musical, curr_weight + 2)

        # 2. Look for musical key names (e.g. 'C# Minor', 'key of Câ™¯m', 'Gb minor')
        for match in _KEY_PATTERN.finditer(text):
            root, accidental, mode = match.group(1), match.group(2), match.group(3)
            normalised = self._normalise_key(root, accidental, mode)
            if normalised in _KEY_TO_CAMELOT:
                cam = _KEY_TO_CAMELOT[normalised]
                curr_musical, curr_weight = counts.get(cam, (normalised.title(), 0))
                # Prefer explicit notation name if discovered
                counts[cam] = (normalised.title(), curr_weight + 1)

        if not counts:
            return None

        best_cam = max(counts, key=lambda k: counts[k][1])
        return counts[best_cam][0], best_cam

    def _extract_bpm_from_text(self, text: str) -> Optional[float]:
        """Extract the most plausible BPM from text using multi-format patterns."""
        bpm_candidates: list[float] = []
        for pattern in _BPM_PATTERNS:
            for match in pattern.finditer(text):
                val = match.group(1)
                if val:
                    try:
                        num = float(val)
                        if 40 <= num <= 240:  # Plausible musical BPM range
                            bpm_candidates.append(num)
                    except ValueError:
                        continue

        if not bpm_candidates:
            return None

        # Return the most frequent BPM rounded to 1 decimal place
        counts: dict[float, int] = {}
        for b in bpm_candidates:
            counts[b] = counts.get(b, 0) + 1
        return max(counts, key=lambda k: counts[k])

    async def get_camelot_key(
        self,
        song_name: str,
        artist: str,
        search_engines: Optional[str] = None,
        max_results: int = 5,
        exclude_domains: Optional[list[str]] = ("tunebat.com", "reddit.com"),
    ) -> Optional[CamelotKey]:
        """Search for and extract the Camelot key and BPM for a given song and artist.

        Queries SearXNG, extracts the musical key and BPM from each result snippet
        individually, then returns the mode (most common values) along with the
        full per-source breakdown.

        Args:
            song_name: The name of the song.
            artist: The name of the artist.
            search_engines: Comma-separated SearXNG engines to use.
                            Defaults to None (uses all configured active engines in SearXNG).
            max_results: Number of search result snippets to scan.
            exclude_domains: List of domains to exclude from search results.
                             Defaults to ("tunebat.com", "reddit.com").

        Returns:
            A CamelotKey instance with sources, or None if neither key nor BPM could be found
            or if SearXNG is temporarily unavailable (e.g. rate limited).

        Raises:
            PermissionError: If SearXNG returns 403 (misconfiguration).
            httpx.HTTPStatusError: If the SearXNG request fails with a non-rate-limit error.
        """
        query = f"{song_name} {artist} camelot key bpm"
        try:
            results = await self._search_searxng(
                query,
                engines=search_engines,
                max_results=max_results,
                exclude_domains=list(exclude_domains) if exclude_domains else None,
            )
        except ValueError:
            # SearXNG returned no results (e.g. engine rate-limited) â€” treat as not found
            return None
        except Exception:
            raise

        sources: list[KeySource] = []
        key_counts: dict[str, int] = {}
        bpm_counts: dict[float, int] = {}

        for r in results:
            content = r.get("content", "")
            if not content:
                continue

            extracted_key = self._extract_key_from_text(content)
            extracted_bpm = self._extract_bpm_from_text(content)

            if extracted_key is None and extracted_bpm is None:
                continue

            musical_key, camelot_key = extracted_key if extracted_key else (None, None)
            if camelot_key:
                key_counts[camelot_key] = key_counts.get(camelot_key, 0) + 1

            if extracted_bpm is not None:
                bpm_counts[extracted_bpm] = bpm_counts.get(extracted_bpm, 0) + 1

            sources.append(
                KeySource(
                    url=r["url"],
                    musical_key=musical_key,
                    camelot_key=camelot_key,
                    bpm=extracted_bpm,
                )
            )

        if not sources:
            return None

        # Determine mode for Camelot Key
        mode_camelot = max(key_counts, key=lambda k: key_counts[k]) if key_counts else None
        mode_key = next((s.musical_key for s in sources if s.camelot_key == mode_camelot), None)

        # Determine mode for BPM
        mode_bpm = max(bpm_counts, key=lambda k: bpm_counts[k]) if bpm_counts else None

        return CamelotKey(
            song_name=song_name,
            artist=artist,
            musical_key=mode_key,
            camelot_key=mode_camelot,
            bpm=mode_bpm,
            sources=sources,
        )


if __name__ == "__main__":
    async def main():
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

