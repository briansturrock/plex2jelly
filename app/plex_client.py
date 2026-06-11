from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class PlexClient:
    base_url: str
    token: str
    timeout: int = 5

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def get_json_or_xml(self, path: str) -> Any:
        headers = {"Accept": "application/json"}
        params = {"X-Plex-Token": self.token}
        response = requests.get(self._url(path), headers=headers, params=params, timeout=self.timeout)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type:
            return response.json()
        try:
            return response.json()
        except ValueError:
            return ET.fromstring(response.text)

    def test(self) -> tuple[bool, str]:
        try:
            data = self.get_json_or_xml("/")
            if isinstance(data, ET.Element):
                name = data.attrib.get("friendlyName") or data.attrib.get("machineIdentifier") or "Plex"
            else:
                container = data.get("MediaContainer", data)
                name = container.get("friendlyName") or container.get("machineIdentifier") or "Plex"
            return True, f"OK ({name})"
        except Exception as exc:
            return False, str(exc)

    def libraries(self) -> list[dict[str, str]]:
        data = self.get_json_or_xml("/library/sections")
        libraries: list[dict[str, str]] = []
        if isinstance(data, ET.Element):
            for item in data.findall("Directory"):
                libraries.append({
                    "key": item.attrib.get("key", ""),
                    "title": item.attrib.get("title", ""),
                    "type": item.attrib.get("type", ""),
                })
        else:
            container = data.get("MediaContainer", data)
            for item in container.get("Directory", []):
                libraries.append({
                    "key": str(item.get("key", "")),
                    "title": item.get("title", ""),
                    "type": item.get("type", ""),
                })
        return libraries

    def library_items(self, library_key: str) -> list[dict[str, object]]:
        data = self.get_json_or_xml(f"/library/sections/{library_key}/all")
        container = data.get("MediaContainer", data) if not isinstance(data, ET.Element) else None
        items: list[dict[str, object]] = []

        if isinstance(data, ET.Element):
            videos = data.findall("Video")
            for video in videos:
                part = video.find("./Media/Part")
                file_path = part.attrib.get("file", "") if part is not None else ""
                items.append({
                    "rating_key": video.attrib.get("ratingKey", ""),
                    "guid": video.attrib.get("guid", ""),
                    "title": video.attrib.get("title", ""),
                    "year": _int_or_none(video.attrib.get("year")),
                    "duration_ms": _int_or_none(video.attrib.get("duration")),
                    "path": file_path,
                    "media_type": video.attrib.get("type", "movie"),
                })
            return items

        for video in container.get("Metadata", []):
            file_path = ""
            media = video.get("Media") or []
            if media:
                parts = media[0].get("Part") or []
                if parts:
                    file_path = parts[0].get("file", "") or ""
            items.append({
                "rating_key": str(video.get("ratingKey", "")),
                "guid": video.get("guid", ""),
                "title": video.get("title", ""),
                "year": _int_or_none(video.get("year")),
                "duration_ms": _int_or_none(video.get("duration")),
                "path": file_path,
                "media_type": video.get("type", "movie"),
            })
        return items

    def item_metadata(self, rating_key: str) -> dict[str, object]:
        data = self.get_json_or_xml(f"/library/metadata/{rating_key}")
        if isinstance(data, ET.Element):
            video = data.find("Video")
            if video is None:
                return {}
            return {
                "title": video.attrib.get("title", ""),
                "sort_title": video.attrib.get("titleSort") or video.attrib.get("title", ""),
                "original_title": video.attrib.get("originalTitle", ""),
                "summary": video.attrib.get("summary", ""),
                "tagline": video.attrib.get("tagline", ""),
                "year": _int_or_none(video.attrib.get("year")),
                "originally_available_at": video.attrib.get("originallyAvailableAt", ""),
                "content_rating": video.attrib.get("contentRating", ""),
                "audience_rating": _float_or_none(video.attrib.get("audienceRating")),
            }

        container = data.get("MediaContainer", data)
        metadata = container.get("Metadata") or []
        if not metadata:
            return {}
        item = metadata[0]
        return {
            "title": item.get("title", ""),
            "sort_title": item.get("titleSort") or item.get("title", ""),
            "original_title": item.get("originalTitle", ""),
            "summary": item.get("summary", ""),
            "tagline": item.get("tagline", ""),
            "year": _int_or_none(item.get("year")),
            "originally_available_at": item.get("originallyAvailableAt", ""),
            "content_rating": item.get("contentRating", ""),
            "audience_rating": _float_or_none(item.get("audienceRating")),
        }


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
