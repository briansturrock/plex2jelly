from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass
class JellyfinClient:
    base_url: str
    api_key: str
    timeout: int = 15

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Emby-Token": self.api_key, "Accept": "application/json"}

    def test(self) -> tuple[bool, str]:
        try:
            response = requests.get(self._url("/System/Info"), headers=self.headers, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            name = data.get("ServerName") or data.get("Id") or "Jellyfin"
            version = data.get("Version", "unknown")
            return True, f"OK ({name}, {version})"
        except Exception as exc:
            return False, str(exc)

    def libraries(self) -> list[dict[str, str]]:
        response = requests.get(self._url("/Library/VirtualFolders"), headers=self.headers, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        libraries: list[dict[str, str]] = []
        for item in data:
            name = item.get("Name", "")
            item_id = item.get("ItemId") or item.get("Id") or name
            collection_type = item.get("CollectionType") or item.get("LibraryOptions", {}).get("TypeOptions", [{}])[0].get("Type", "")
            libraries.append({"id": str(item_id), "name": name, "type": collection_type or "unknown"})
        return libraries
