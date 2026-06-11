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
    library_mapping_id INTEGER,
    plex_path TEXT NOT NULL,
    jellyfin_path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovered_libraries (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'unknown',
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source, source_id)
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
    jellyfin_title TEXT,
    jellyfin_year INTEGER,
    jellyfin_duration_ms INTEGER,
    match_warning TEXT,
    tmdb_id TEXT,
    imdb_id TEXT,
    tmdb_lookup_status TEXT NOT NULL DEFAULT 'not_attempted',
    metadata_hash TEXT,
    artwork_hash TEXT,
    identity_synced_at TEXT,
    identity_sync_status TEXT,
    identity_sync_error TEXT,
    match_status TEXT NOT NULL DEFAULT 'unknown',
    last_seen_at TEXT,
    last_synced_at TEXT,
    UNIQUE(library_mapping_id, canonical_path)
);

CREATE TABLE IF NOT EXISTS sync_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_item_id INTEGER,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _run_migrations(conn: sqlite3.Connection) -> None:
    if not _column_exists(conn, "path_mappings", "library_mapping_id"):
        conn.execute("ALTER TABLE path_mappings ADD COLUMN library_mapping_id INTEGER")
    for column, ddl in [
        ("jellyfin_title", "ALTER TABLE sync_items ADD COLUMN jellyfin_title TEXT"),
        ("jellyfin_year", "ALTER TABLE sync_items ADD COLUMN jellyfin_year INTEGER"),
        ("jellyfin_duration_ms", "ALTER TABLE sync_items ADD COLUMN jellyfin_duration_ms INTEGER"),
        ("match_warning", "ALTER TABLE sync_items ADD COLUMN match_warning TEXT"),
        ("identity_synced_at", "ALTER TABLE sync_items ADD COLUMN identity_synced_at TEXT"),
        ("identity_sync_status", "ALTER TABLE sync_items ADD COLUMN identity_sync_status TEXT"),
        ("identity_sync_error", "ALTER TABLE sync_items ADD COLUMN identity_sync_error TEXT"),
    ]:
        if not _column_exists(conn, "sync_items", column):
            conn.execute(ddl)


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _run_migrations(conn)


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


def replace_discovered_libraries(source: str, libraries: list[dict[str, str]]) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM discovered_libraries WHERE source = ?", (source,))
        for item in libraries:
            source_id = str(item.get("key") or item.get("id") or "").strip()
            name = str(item.get("title") or item.get("name") or "").strip()
            media_type = str(item.get("type") or "unknown").strip() or "unknown"
            if not source_id or not name:
                continue
            conn.execute(
                """
                INSERT INTO discovered_libraries(source, source_id, name, media_type, last_seen_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (source, source_id, name, media_type),
            )


def get_discovered_libraries(source: str) -> list[sqlite3.Row]:
    init_db()
    with connect() as conn:
        return conn.execute(
            """
            SELECT source, source_id, name, media_type, last_seen_at
            FROM discovered_libraries
            WHERE source = ?
            ORDER BY name COLLATE NOCASE
            """,
            (source,),
        ).fetchall()
