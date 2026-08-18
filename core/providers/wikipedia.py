from io import StringIO
from urllib.parse import quote

import requests
import pandas as pd

class WikipediaProvider:
    """Provider for Wikipedia API."""

    def __init__(self, api_url: str = "https://en.wikipedia.org/api/rest_v1"):
        self.api_url = api_url

    def _get_page_html(self, page_title: str) -> str:
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

    def _parse_tables_from_html(self, html_content: str) -> list[pd.DataFrame]:
        """Parse tables from the HTML content of a Wikipedia page."""
        tables = pd.read_html(StringIO(html_content))
        normalized_tables = []
        for table in tables:
            # If columns are MultiIndex, flatten to top level
            if isinstance(table.columns, pd.MultiIndex):
                table.columns = [str(col[0]).strip() for col in table.columns]
            else:
                table.columns = [str(col).strip() for col in table.columns]
            normalized_tables.append(table)

        return normalized_tables

    def _get_tables(
        self,
        page_title: str,
        track_metadata_only: bool,
        track_metadata_columns: list[str],
        title_column: str = "Song",
    ) -> list[pd.DataFrame]:
        """A generic method to fetch and parse tables from a Wikipedia page."""
        html_content = self._get_page_html(page_title)
        tables = self._parse_tables_from_html(html_content)

        # Filter only tables containing all required track metadata columns
        matching_tables = [
            table for table in tables
            if all(col in table.columns for col in track_metadata_columns)
        ]

        if track_metadata_only:
            processed_tables = []
            for table in matching_tables:
                df = table[track_metadata_columns].copy()

                if title_column in df.columns:
                    # Drop rows where the title column does not include quotes ("")
                    has_quotes = df[title_column].astype(str).str.contains(r'"[^"]*"', regex=True, na=False)
                    df = df[has_quotes].copy()

                    # Extract only the text within the quotes (e.g. '"ABC"' -> 'ABC')
                    df[title_column] = df[title_column].astype(str).str.extract(r'"([^"]*)"', expand=False)

                # Drop duplicates
                df = df.drop_duplicates()
                processed_tables.append(df)

            return processed_tables

        return matching_tables

    def get_billboard_streaming_songs_number_ones(
        self, year: int, track_metadata_only: bool = False
    ) -> list[pd.DataFrame]:
        """Get the Billboard Streaming Songs number ones for a given year."""
        page_title = f"List of Billboard Streaming Songs number ones of {year}"
        title_column: str = "Song"

        return self._get_tables(
            page_title,
            track_metadata_only,
            track_metadata_columns=[title_column, "Artist(s)"],
            title_column=title_column,
        )

    def get_billboard_hot_100_number_ones(
        self, year: int, track_metadata_only: bool = False
    ) -> list[pd.DataFrame]:
        """Get the Billboard Hot 100 number ones for a given year."""
        page_title = f"List of Billboard Hot 100 number ones of {year}"
        title_column: str = "Song"

        return self._get_tables(
            page_title,
            track_metadata_only,
            track_metadata_columns=[title_column, "Artist(s)"],
            title_column=title_column,
        )

    def get_billboard_hot_100_top_ten_singles(
        self, year: int, track_metadata_only: bool = False
    ) -> list[pd.DataFrame]:
        """Get the Billboard Hot 100 top-ten singles for a given year."""
        page_title = f"List of Billboard Hot 100 top-ten singles in {year}"
        title_column: str = "Single"
        
        return self._get_tables(
            page_title,
            track_metadata_only,
            track_metadata_columns=[title_column, "Artist(s)"],
            title_column=title_column,
        )


if __name__ == "__main__":
    provider = WikipediaProvider()
    year = 2023
    tables = provider.get_billboard_hot_100_top_ten_singles(year, track_metadata_only=True)

    print(f"Found {len(tables)} matching tables.")
    print(tables[0].head().to_string())  # Print the first table for demonstration

