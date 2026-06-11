from __future__ import annotations

from app.config import AppConfig
from app.database import check_database
from app.jellyfin_client import JellyfinClient
from app.plex_client import PlexClient


def _print_status(name: str, ok: bool, message: str) -> None:
    state = "OK" if ok else "ERROR"
    print(f"{name:<9} {state:<5} {message}")


def run_health(config: AppConfig) -> int:
    print("plex2jelly health")
    print("-----------------")

    db_ok, db_message = check_database(config.database.path)
    plex_ok, plex_message = PlexClient(config.plex).health()
    jellyfin_ok, jellyfin_message = JellyfinClient(config.jellyfin).health()

    _print_status("Config", True, "OK")
    _print_status("Database", db_ok, db_message)
    _print_status("Plex", plex_ok, plex_message)
    _print_status("Jellyfin", jellyfin_ok, jellyfin_message)

    return 0 if db_ok and plex_ok and jellyfin_ok else 1
