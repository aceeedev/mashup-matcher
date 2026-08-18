import requests
import dotenv
from typing import Optional
import os

dotenv.load_dotenv()

class MetaBrainzProvider:
    """Client for fetching data from the MetaBrainz API."""

    def __init__(self, api_url: str = "https://musicbrainz.org/ws/2"):
        """Initialize the provider with the MetaBrainz API base URL."""
        self.api_url = api_url
        self.headers = {
            "User-Agent": f"MashupMatcher/0.1.0 ( {os.getenv('EMAIL', 'mashupmatcher@example.com')} )"
        }


    def search_recording(self, track_name: Optional[str] = None, artist_name: Optional[str] = None) -> Optional[dict]:
        """Search for a recording by track name and artist name."""
        url = f"{self.api_url}/recording"

        if not track_name and not artist_name:
            raise ValueError("At least one of track_name or artist_name must be provided.")
        if track_name and not artist_name:
            query = f'recording:"{track_name}"'
        elif artist_name and not track_name:
            query = f'artist:"{artist_name}"'
        else:
            query = f'recording:"{track_name}" AND artist:"{artist_name}"'

        params = {
            "query": query,
            "fmt": "json",
            "limit": 1
        }

        response = requests.get(url, headers=self.headers, params=params)
        if response.status_code == 200:
            data = response.json()
            recordings = data.get("recordings", [])
            
            if recordings:
                best_match = recordings[0]

                return best_match
        else:
            response.raise_for_status()

        return None


if __name__ == "__main__":
    provider = MetaBrainzProvider()

    print(provider.search_recording("Bohemian Rhapsody", "Queen"))
