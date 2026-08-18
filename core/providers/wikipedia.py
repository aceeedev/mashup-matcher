from io import StringIO
from urllib.parse import quote

import requests
import pandas as pd

class WikipediaProvider:
    """Provider for Wikipedia API."""

    def __init__(self, api_url: str = "https://en.wikipedia.org/api/rest_v1"):
        self.api_url = api_url

    def get_page_html(self, page_title: str) -> str:
        """Get the HTML content of a Wikipedia page for a given title."""
        url = f"{self.api_url}/page/html/{quote(page_title, safe='')}"
        headers = {
            "User-Agent": "https://github.com/aceeedev/mashup-matcher",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            return response.text
        else:
            response.raise_for_status()

    def parse_tables_from_html(self, html_content: str) -> list[pd.DataFrame]:
        """Parse tables from the HTML content of a Wikipedia page."""
        tables = pd.read_html(StringIO(html_content))

        target_columns = ["Issue date", "Song", "Artist(s)", "Weekly streams"]
        matching_tables = [
            table for table in tables if list(table.columns) == target_columns
        ]

        return matching_tables or tables
    
    def get_billboard_streaming_songs_number_ones(self, year: int) -> list[pd.DataFrame]:
        """Get the Billboard Streaming Songs number ones for a given year."""
        page_title = f"List of Billboard Streaming Songs number ones of {year}"
        html_content = self.get_page_html(page_title)
        tables = self.parse_tables_from_html(html_content)
        return tables


if __name__ == "__main__":
    provider = WikipediaProvider()
    year = 2023
    tables = provider.get_billboard_streaming_songs_number_ones(year)

    print(tables[0].head())  # Print the first table for demonstration