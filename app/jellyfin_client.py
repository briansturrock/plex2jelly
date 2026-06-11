from __future__ import annotations

import requests

from app.config import ServerConfig


class JellyfinClient:
    def __init__(self, config: ServerConfig, timeout: int = 15) -> None:
        self.config = config
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "X-Emby-Token": self.config.api_key or "",
            "Accept": "application/json",
        }

    def _get(self, path: str) -> requests.Response:
        response = requests.get(
            f"{self.config.base_url}{path}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def health(self) -> tuple[bool, str]:
        try:
            info = self._get("/System/Info/Public").json()
            name = info.get("ServerName") or "Jellyfin"
            version = info.get("Version", "unknown")
            return True, f"OK ({name}, version {version})"
        except Exception as exc:
            return False, str(exc)

    def libraries(self) -> list[dict[str, str]]:
        data = self._get("/Library/VirtualFolders").json()
        return [
            {
                "name": str(item.get("Name", "")),
                "collection_type": str(item.get("CollectionType", "")),
            }
            for item in data
        ]
