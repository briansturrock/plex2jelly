from __future__ import annotations

from app.config import AppConfig
from app.jellyfin_client import JellyfinClient
from app.plex_client import PlexClient


def run_libraries(config: AppConfig) -> int:
    plex = PlexClient(config.plex)
    jellyfin = JellyfinClient(config.jellyfin)

    print("Plex libraries")
    print("--------------")
    for library in plex.libraries():
        print(f"- {library['title']} ({library['type']}, key {library['key']})")

    print()
    print("Jellyfin libraries")
    print("------------------")
    for library in jellyfin.libraries():
        collection_type = library["collection_type"] or "unknown"
        print(f"- {library['name']} ({collection_type})")

    return 0
