import os
from typing import Optional
from urllib.parse import quote_plus

import dotenv
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

dotenv.load_dotenv()


class DatabaseManager:
    """Owns the MongoDB connection and the indexes the app's queries rely on."""

    def __init__(
        self,
        uri: Optional[str] = None,
        db_name: str = "mashup_db",
        client: Optional[MongoClient] = None,
    ):
        """Initialize the connection.

        Falls back to building a URI from the MONGO_USERNAME/MONGO_PASSWORD env vars
        (as set in .env, matching compose.yml's mongodb service) plus an optional
        MONGO_HOST (default localhost:27017), unless a full `uri` or an existing
        `client` is given.

        Args:
            uri: A full mongodb:// connection string. Overrides the env-based build.
            db_name: The database to use.
            client: An existing MongoClient to reuse instead of creating one.
        """
        # tz_aware=True so datetimes read back from Mongo are UTC-aware, matching what
        # models.track writes (datetime.now(timezone.utc)) - avoids naive/aware mismatches later.
        self.client = client or MongoClient(uri or self._build_uri_from_env(), tz_aware=True)
        self.db: Database = self.client[db_name]

    @staticmethod
    def _build_uri_from_env() -> str:
        username = os.getenv("MONGO_USERNAME")
        password = os.getenv("MONGO_PASSWORD")
        host = os.getenv("MONGO_HOST", "localhost:27017")

        if not username or not password:
            raise ValueError(
                "MONGO_USERNAME and MONGO_PASSWORD must be set (see .env / compose.yml) "
                "unless a `uri` or `client` is passed explicitly."
            )

        return f"mongodb://{quote_plus(username)}:{quote_plus(password)}@{host}/?authSource=admin"

    @property
    def tracks(self) -> Collection:
        return self.db["tracks"]

    @property
    def search_cache(self) -> Collection:
        return self.db["search_cache"]

    @property
    def mashup_ideas(self) -> Collection:
        return self.db["mashup_ideas"]

    def ensure_indexes(self) -> None:
        """Create the indexes the app's queries and dedup logic rely on. Safe to call repeatedly."""
        self.tracks.create_index([("track_key", ASCENDING)], unique=True)
        self.tracks.create_index(
            [("external_ids.musicbrainz_recording_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"external_ids.musicbrainz_recording_id": {"$type": "string"}},
        )
        self.tracks.create_index([("consensus.camelot_key", ASCENDING), ("consensus.bpm_folded", ASCENDING)])
        self.tracks.create_index([("enrichment.status", ASCENDING), ("enrichment.next_attempt_at", ASCENDING)])

        self.search_cache.create_index([("track_id", ASCENDING), ("provider", ASCENDING)], unique=True)

        self.mashup_ideas.create_index([("pair_key", ASCENDING)], unique=True)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()


if __name__ == "__main__":
    db_manager = DatabaseManager()
    db_manager.ensure_indexes()
    print(f"Connected to '{db_manager.db.name}'. Collections: {db_manager.db.list_collection_names()}")
