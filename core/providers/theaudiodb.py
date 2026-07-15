import requests
from typing import Optional

class TheAudioDBProvider:
    """Client for fetching TheAudioDB data."""

    def __init__(self, api_url: str = "https://theaudiodb.com/api/v1/json", api_key: str = "123"):
        """Initialize the provider with the TheAudioDB API base URL."""
        self.api_key = api_key
        self.api_url = f"{api_url}/{self.api_key}"


    def search_track(self, artist_query: str, track_query: str) -> dict:
        url = f"{self.api_url}/searchtrack.php"
        query_params = {
            "s": artist_query,
            "t": track_query
        }

        response = requests.get(url, params=query_params)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()

    def lookup_track(
        self,
        album_id: Optional[str] = None,
        track_id: Optional[str] = None,
        music_brainz_recording_id: Optional[str] = None,
    ) -> dict:
        """Lookup track details by album ID, track ID, or MusicBrainz recording ID.

        Exactly one identifier must be provided.
        """
        provided_ids = [value for value in (album_id, track_id, music_brainz_recording_id) if value]
        if len(provided_ids) != 1:
            raise ValueError("Provide exactly one of album_id, track_id, or music_brainz_recording_id.")

        if album_id:
            url = f"{self.api_url}/track.php"
            query_params = {"m": album_id}
        elif track_id:
            url = f"{self.api_url}/track.php"
            query_params = {"h": track_id}
        else:
            url = f"{self.api_url}/track-mb.php"
            query_params = {"i": music_brainz_recording_id}

        response = requests.get(url, params=query_params)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()

    def get_track_top10(
        self,
        artist_name: Optional[str] = None,
        music_brainz_artist_id: Optional[str] = None,
    ) -> dict:
        """Return the top 10 tracks for an artist by name or MusicBrainz artist ID."""
        provided_ids = [value for value in (artist_name, music_brainz_artist_id) if value]
        if len(provided_ids) != 1:
            raise ValueError("Provide exactly one of artist_name or music_brainz_artist_id.")

        if artist_name:
            url = f"{self.api_url}/track-top10.php"
            query_params = {"s": artist_name}
        else:
            url = f"{self.api_url}/track-top10-mb.php"
            query_params = {"s": music_brainz_artist_id}

        response = requests.get(url, params=query_params)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()
    
    def get_trending(self, country: str, source_type: str, media_format: str) -> dict:
        """Return trending releases for a country, source type, and media format."""
        url = f"{self.api_url}/trending.php"
        query_params = {
            "country": country,
            "type": source_type,
            "format": media_format,
        }

        response = requests.get(url, params=query_params)
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()



if __name__ == "__main__":
    provider = TheAudioDBProvider()
    album_id = "2115888"
    track_id = "32793500"
    music_brainz_recording_id = "50369905-68ca-48d2-912d-b37330ff7dc3"
    music_brainz_artist_id = "cc197bad-dc9c-440d-a5b5-d52ba2e14234"

    def print_call(label: str, callback) -> None:
        try:
            print(label + ":", callback())
        except requests.HTTPError as error:
            print(label + " failed:", error)

        print()

    print_call("search_track", lambda: provider.search_track(artist_query="coldplay", track_query="yellow"))
    print_call("lookup_track album_id", lambda: provider.lookup_track(album_id=album_id))
    print_call("lookup_track track_id", lambda: provider.lookup_track(track_id=track_id))
    print_call(
        "lookup_track music_brainz_recording_id",
        lambda: provider.lookup_track(music_brainz_recording_id=music_brainz_recording_id),
    )
    print_call("get_track_top10 artist_name", lambda: provider.get_track_top10(artist_name="coldplay"))
    print_call(
        "get_track_top10 music_brainz_artist_id",
        lambda: provider.get_track_top10(music_brainz_artist_id=music_brainz_artist_id),
    )
    print_call(
        "get_trending albums",
        lambda: provider.get_trending(country="us", source_type="itunes", media_format="albums"),
    )
    print_call(
        "get_trending singles",
        lambda: provider.get_trending(country="us", source_type="itunes", media_format="singles"),
    )
