from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class JellyfinClient:
    base_url: str
    api_key: str
    timeout: int = 5

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Emby-Token": self.api_key,
            "X-MediaBrowser-Token": self.api_key,
            "Accept": "application/json",
        }

    @property
    def write_headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                'MediaBrowser Client="plex2jelly", '
                'Device="plex2jelly", '
                'DeviceId="plex2jelly-docker", '
                'Version="0.3.8", '
                f'Token="{self.api_key}"'
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

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

    def library_items(self, library_id: str, media_type: str = "movie") -> list[dict[str, object]]:
        include_types = "Movie" if media_type == "movie" else "Movie,Series,Episode"
        params = {
            "ParentId": library_id,
            "Recursive": "true",
            "IncludeItemTypes": include_types,
            "Fields": "Path,ProductionYear,RunTimeTicks",
        }
        response = requests.get(self._url("/Items"), headers=self.headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()

        items: list[dict[str, object]] = []
        for item in data.get("Items", []):
            ticks = item.get("RunTimeTicks")
            duration_ms = int(ticks / 10000) if isinstance(ticks, int) else None
            items.append(
                {
                    "item_id": item.get("Id", ""),
                    "title": item.get("Name", ""),
                    "year": _int_or_none(item.get("ProductionYear")),
                    "duration_ms": duration_ms,
                    "path": item.get("Path", "") or "",
                    "media_type": item.get("Type", ""),
                }
            )
        return items

    def get_item(self, item_id: str) -> dict[str, object]:
        response = requests.get(self._url(f"/Items/{item_id}"), headers=self.headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def update_item(self, item_id: str, item: dict[str, Any]) -> None:
        response = requests.post(self._url(f"/Items/{item_id}"), headers=self.write_headers, json=item, timeout=self.timeout)
        self._raise_update_error_if_needed(response, item)

    def update_item_metadata_editor_payload(self, item_id: str, item: dict[str, Any]) -> None:
        response = requests.post(self._url(f"/Items/{item_id}"), headers=self.write_headers, json=item, timeout=self.timeout)
        self._raise_update_error_if_needed(response, item)

    def _raise_update_error_if_needed(self, response: requests.Response, item: dict[str, Any]) -> None:
        if response.ok:
            return

        body = (response.text or "").strip().replace("\n", " ")
        if len(body) > 800:
            body = body[:800] + "..."

        fields = ", ".join(sorted(item.keys()))
        raise RuntimeError(
            f"Jellyfin update failed: HTTP {response.status_code} {response.reason}. "
            f"Fields sent: {fields}. Response: {body or ''}"
        )


def _int_or_none(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
