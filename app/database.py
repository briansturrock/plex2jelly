from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DEFAULT_DB_PATH = os.getenv("PLEX2JELLY_DB", "/data/plex2jelly.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS path_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plex_path TEXT NOT NULL,
    jellyfin_path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS library_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    plex_library_key TEXT NOT NULL,
    plex_library_name TEXT NOT NULL,
    jellyfin_library_id TEXT NOT NULL,
    jellyfin_library_name TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'unknown',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_mapping_id INTEGER NOT NULL,
    plex_rating_key TEXT,
    plex_guid TEXT,
    jellyfin_item_id TEXT,
    media_type TEXT,
    canonical_path TEXT,
    plex_path TEXT,
    jellyfin_path TEXT,
    plex_title TEXT,
    plex_year INTEGER,
    plex_duration_ms INTEGER,
    tmdb_id TEXT,
    imdb_id TEXT,
    tmdb_lookup_status TEXT NOT NULL DEFAULT 'not_attempted',
    metadata_hash TEXT,
    artwork_hash TEXT,
    match_status TEXT NOT NULL DEFAULT 'unknown',
    last_seen_at TEXT,
    last_synced_at TEXT,
    UNIQUE(library_mapping_id, canonical_path)
);
"""


def get_db_path() -> str:
    return os.getenv("PLEX2JELLY_DB", DEFAULT_DB_PATH)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    db_path = get_db_path()
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def get_setting(key: str, default: str = "") -> str:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value or ""),
        )


def get_settings() -> dict[str, str]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}
