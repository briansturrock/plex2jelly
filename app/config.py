from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = "/config/config.yml"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    token: str | None = None
    api_key: str | None = None


@dataclass(frozen=True)
class DatabaseConfig:
    path: str


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    plex: ServerConfig
    jellyfin: ServerConfig
    database: DatabaseConfig


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"Missing or invalid config section: {name}")
    return value


def _require_str(section: dict[str, Any], key: str, section_name: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Missing or invalid config value: {section_name}.{key}")
    return value.strip().rstrip("/")


def load_config(path: str | None = None) -> AppConfig:
    config_path = Path(path or os.environ.get("PLEX2JELLY_CONFIG", DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    plex_raw = _require_mapping(raw.get("plex"), "plex")
    jellyfin_raw = _require_mapping(raw.get("jellyfin"), "jellyfin")
    database_raw = _require_mapping(raw.get("database"), "database")

    plex = ServerConfig(
        base_url=_require_str(plex_raw, "base_url", "plex"),
        token=_require_str(plex_raw, "token", "plex"),
    )
    jellyfin = ServerConfig(
        base_url=_require_str(jellyfin_raw, "base_url", "jellyfin"),
        api_key=_require_str(jellyfin_raw, "api_key", "jellyfin"),
    )
    database = DatabaseConfig(path=_require_str(database_raw, "path", "database"))

    return AppConfig(raw=raw, plex=plex, jellyfin=jellyfin, database=database)
