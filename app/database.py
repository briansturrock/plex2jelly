from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 1);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plex_rating_key TEXT,
    plex_guid TEXT,
    jellyfin_item_id TEXT,
    media_type TEXT,
    canonical_path TEXT UNIQUE,
    plex_path TEXT,
    jellyfin_path TEXT,
    plex_title TEXT,
    plex_year INTEGER,
    plex_duration_ms INTEGER,
    tmdb_id TEXT,
    imdb_id TEXT,
    tmdb_lookup_status TEXT NOT NULL DEFAULT 'not_attempted',
    tmdb_lookup_confidence REAL,
    tmdb_last_checked_at TEXT,
    metadata_hash TEXT,
    artwork_hash TEXT,
    match_status TEXT NOT NULL DEFAULT 'unknown',
    last_seen_at TEXT,
    last_synced_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS watch_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    plex_user_id TEXT,
    jellyfin_user_id TEXT,
    plex_view_count INTEGER,
    plex_view_offset_ms INTEGER,
    plex_last_viewed_at TEXT,
    jellyfin_played INTEGER,
    jellyfin_play_count INTEGER,
    jellyfin_playback_position_ticks INTEGER,
    jellyfin_last_played_date TEXT,
    last_synced_plex_state_hash TEXT,
    last_synced_jellyfin_state_hash TEXT,
    last_synced_at TEXT,
    last_direction TEXT,
    conflict_status TEXT,
    FOREIGN KEY (item_id) REFERENCES items(id)
);
"""


def initialise_database(path: str) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def check_database(path: str) -> tuple[bool, str]:
    try:
        initialise_database(path)
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        version = row[0] if row else "unknown"
        return True, f"OK (schema version {version})"
    except Exception as exc:  # pragma: no cover - health command reports the exact error
        return False, str(exc)
