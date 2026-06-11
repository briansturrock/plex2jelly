from __future__ import annotations

import requests

from app.config import ServerConfig


class PlexClient:
    def __init__(self, config: ServerConfig, timeout: int = 15) -> None:
        self.config = config
        self.timeout = timeout

    def _get(self, path: str) -> requests.Response:
        headers = {"Accept": "application/json"}
        params = {"X-Plex-Token": self.config.token}
        response = requests.get(
            f"{self.config.base_url}{path}",
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def health(self) -> tuple[bool, str]:
        try:
            data = self._get("/").json().get("MediaContainer", {})
            name = data.get("friendlyName") or data.get("machineIdentifier") or "Plex"
            version = data.get("version", "unknown")
            return True, f"OK ({name}, version {version})"
        except Exception as exc:
            return False, str(exc)

    def libraries(self) -> list[dict[str, str]]:
        data = self._get("/library/sections").json()
        directories = data.get("MediaContainer", {}).get("Directory", [])
        return [
            {
                "key": str(item.get("key", "")),
                "title": str(item.get("title", "")),
                "type": str(item.get("type", "")),
            }
            for item in directories
        ]
