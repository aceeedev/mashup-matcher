import requests

class AcousticBrainzProvider:
    """Client for fetching AcousticBrainz low-level and high-level data."""

    def __init__(self, api_url: str = "https://acousticbrainz.org/api/v1"):
        """Initialize the provider with the AcousticBrainz API base URL."""
        self.api_url = api_url

    def get_single_tracks(self, mbid: str) -> dict:
        """Fetch low-level data for a single MusicBrainz recording ID."""
        url = f"{self.api_url}/{mbid}/low-level"

        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()

    def get_multiple_tracks(self, mbids: list[str]) -> dict:
        """Fetch low-level data for multiple recording IDs in one request."""
        url = f"{self.api_url}/low-level"
        query_params = {"recording_ids": ";".join(f"{mbid}:0" for mbid in mbids)}

        response = requests.get(url, params=query_params)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()


if __name__ == "__main__":
    provider = AcousticBrainzProvider()
    mbid = "b1a9c0e9-d987-4042-ae91-78d6a3267d69" # Bohemian Rhapsody
    
    single_track_data = provider.get_single_tracks(mbid)
    print(single_track_data)

    mbids = [
        mbid
    ]
    multiple_tracks_data = provider.get_multiple_tracks(mbids)
    print(multiple_tracks_data)