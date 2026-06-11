from __future__ import annotations

from dataclasses import dataclass

from app.database import get_settings


@dataclass
class AppConfig:
    plex_base_url: str = ""
    plex_token: str = ""
    jellyfin_base_url: str = ""
    jellyfin_api_key: str = ""

    @classmethod
    def load(cls) -> "AppConfig":
        settings = get_settings()
        return cls(
            plex_base_url=settings.get("plex_base_url", ""),
            plex_token=settings.get("plex_token", ""),
            jellyfin_base_url=settings.get("jellyfin_base_url", ""),
            jellyfin_api_key=settings.get("jellyfin_api_key", ""),
        )

    @property
    def has_plex(self) -> bool:
        return bool(self.plex_base_url and self.plex_token)

    @property
    def has_jellyfin(self) -> bool:
        return bool(self.jellyfin_base_url and self.jellyfin_api_key)
