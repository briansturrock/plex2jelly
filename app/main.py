from __future__ import annotations

import argparse
import sys

from app.commands.health import run_health
from app.commands.libraries import run_libraries
from app.config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plex2jelly")
    parser.add_argument("--config", help="Path to config.yml")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="Check config, database, Plex and Jellyfin")
    subparsers.add_parser("libraries", help="List Plex and Jellyfin libraries")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config: ERROR ({exc})", file=sys.stderr)
        return 2

    if args.command == "health":
        return run_health(config)
    if args.command == "libraries":
        return run_libraries(config)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
