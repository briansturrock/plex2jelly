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
            "Authorization": (
                'MediaBrowser Client="plex2jelly", Device="plex2jelly", '
                'DeviceId="plex2jelly", Version="0.3.3", Token="' + self.api_key + '"'
            ),
            "Accept": "application/json",
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

    def update_item(self, item_id: str, item: dict[str, object]) -> None:
        """Update a Jellyfin item using a sanitised metadata-edit payload.

        Jellyfin's item update endpoint is fussy about the BaseItemDto body. Posting
        the raw object returned by /Items/{id} can include read-only/nested fields
        that cause HTTP 400 responses. Build a small, metadata-editor-style body
        with the editable identity fields we currently support.
        """
        headers = {**self.headers, "Content-Type": "application/json"}
        payload = _build_item_update_payload(item_id, item)

        response = requests.post(self._url(f"/Items/{item_id}"), headers=headers, json=payload, timeout=self.timeout)
        if not response.ok:
            body = (response.text or "").strip().replace("\n", " ")
            if len(body) > 500:
                body = body[:500] + "..."
            raise RuntimeError(
                f"Jellyfin update failed: HTTP {response.status_code} {response.reason}. "
                f"Payload fields: {', '.join(sorted(payload.keys()))}. {body}"
            )


def _build_item_update_payload(item_id: str, item: dict[str, object]) -> dict[str, object]:
    title = _string(item.get("Name"))
    original_title = _string(item.get("OriginalTitle"))
    sort_name = _string(item.get("ForcedSortName") or item.get("SortName"))
    overview = _string(item.get("Overview"))
    official_rating = _string(item.get("OfficialRating"))
    custom_rating = _string(item.get("CustomRating"))
    premiere_date = _string(item.get("PremiereDate"))

    taglines = _list_of_strings(item.get("Taglines"))
    if not taglines and item.get("Tagline"):
        taglines = [_string(item.get("Tagline"))]

    payload: dict[str, object] = {
        "Id": item_id,
        "Name": title,
        "OriginalTitle": original_title,
        "ForcedSortName": sort_name,
        "Overview": overview,
        "Taglines": taglines,
        "ProductionYear": _int_or_none(item.get("ProductionYear")),
        "PremiereDate": premiere_date or None,
        "OfficialRating": official_rating,
        "CustomRating": custom_rating,
        "CommunityRating": _float_or_none(item.get("CommunityRating")),
        "CriticRating": _float_or_none(item.get("CriticRating")),
        "ProviderIds": _dict_or_empty(item.get("ProviderIds")),
        "Genres": _list_of_strings(item.get("Genres")),
        "Tags": _list_of_strings(item.get("Tags")),
        "Studios": _list_of_name_objects(item.get("Studios")),
        "People": _list_of_people(item.get("People")),
        "LockedFields": _list_of_strings(item.get("LockedFields")),
        "LockData": bool(item.get("LockData") or False),
    }

    # Keep useful optional fields when Jellyfin supplied them. Avoid copying large
    # read-only graph fields such as MediaSources, ImageTags, Chapters, Trickplay,
    # UserData, etc.
    for key in (
        "DisplayOrder",
        "Status",
        "AirTime",
        "AspectRatio",
        "Video3DFormat",
        "PreferredMetadataLanguage",
        "PreferredMetadataCountryCode",
    ):
        if key in item:
            payload[key] = item.get(key)

    # Jellyfin expects lists rather than null for many metadata editor fields.
    for key in ("Genres", "Tags", "Studios", "People", "LockedFields", "Taglines"):
        if payload.get(key) is None:
            payload[key] = []

    return payload


def _string(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and item.get("Name"):
            result.append(str(item["Name"]))
        else:
            result.append(str(item))
    return result


def _list_of_name_objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("Name") or item.get("name")
            if name:
                result.append({"Name": str(name), **{k: v for k, v in item.items() if k != "Name"}})
        elif item:
            result.append({"Name": str(item)})
    return result


def _list_of_people(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for person in value:
        if not isinstance(person, dict):
            continue
        name = person.get("Name")
        if not name:
            continue
        clean = {
            "Name": str(name),
            "Type": str(person.get("Type") or "Actor"),
        }
        if person.get("Role"):
            clean["Role"] = str(person.get("Role"))
        if person.get("SortOrder") is not None:
            clean["SortOrder"] = person.get("SortOrder")
        result.append(clean)
    return result


def _int_or_none(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
