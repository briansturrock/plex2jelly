from __future__ import annotations

import argparse

from app.config import AppConfig
from app.database import init_db
from app.jellyfin_client import JellyfinClient
from app.plex_client import PlexClient


def cmd_health() -> int:
    init_db()
    cfg = AppConfig.load()
    print("plex2jelly health")
    print("-----------------")
    print("Database  OK    OK")

    if cfg.has_plex:
        ok, msg = PlexClient(cfg.plex_base_url, cfg.plex_token).test()
        print(f"Plex      {'OK' if ok else 'FAIL'}  {msg}")
    else:
        print("Plex      WARN  Not configured")

    if cfg.has_jellyfin:
        ok, msg = JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key).test()
        print(f"Jellyfin  {'OK' if ok else 'FAIL'}  {msg}")
    else:
        print("Jellyfin  WARN  Not configured")
    return 0


def cmd_libraries() -> int:
    cfg = AppConfig.load()
    if not cfg.has_plex or not cfg.has_jellyfin:
        print("Plex and Jellyfin must be configured in the UI first.")
        return 1

    print("Plex libraries")
    for item in PlexClient(cfg.plex_base_url, cfg.plex_token).libraries():
        print(f"- {item['title']} ({item['type']}) key={item['key']}")

    print("\nJellyfin libraries")
    for item in JellyfinClient(cfg.jellyfin_base_url, cfg.jellyfin_api_key).libraries():
        print(f"- {item['name']} ({item['type']}) id={item['id']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="plex2jelly")
    parser.add_argument("command", choices=["health", "libraries"], help="Command to run")
    args = parser.parse_args()

    if args.command == "health":
        return cmd_health()
    if args.command == "libraries":
        return cmd_libraries()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
