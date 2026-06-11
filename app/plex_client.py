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
            ratings = _xml_child_dicts(video, "Rating")
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
                "critic_rating": _preferred_critic_rating(ratings),
                "genres": _xml_tags(video, "Genre"),
                "countries": _xml_tags(video, "Country"),
                "studios": _dedupe_strings([video.attrib.get("studio", ""), *_xml_tags(video, "Studio")]),
                "provider_ids": _provider_ids_from_guids(_xml_guid_ids(video)),
            }

        container = data.get("MediaContainer", data)
        metadata = container.get("Metadata") or []
        if not metadata:
            return {}
        item = metadata[0]
        ratings = item.get("Rating") or []
        studio = item.get("studio", "")
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
            "critic_rating": _preferred_critic_rating(ratings),
            "genres": _tag_values(item.get("Genre")),
            "countries": _tag_values(item.get("Country")),
            "studios": _dedupe_strings([studio, *_tag_values(item.get("Studio"))]),
            "provider_ids": _provider_ids_from_guids(_guid_values(item.get("Guid"))),
        }


def _tag_values(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("tag") or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def _guid_values(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    values: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("id") or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def _provider_ids_from_guids(guids: list[str]) -> dict[str, str]:
    provider_ids: dict[str, str] = {}
    for guid in guids:
        if "://" not in guid:
            continue
        provider, value = guid.split("://", 1)
        provider = provider.strip().lower()
        value = value.strip()
        if provider in {"imdb", "tmdb", "tvdb"} and value:
            provider_ids[provider] = value
    return provider_ids


def _preferred_critic_rating(ratings: object) -> float | None:
    if not isinstance(ratings, list):
        return None

    def rating_value(item: object) -> float | None:
        if not isinstance(item, dict):
            return None
        if str(item.get("type") or "").lower() != "critic":
            return None
        return _float_or_none(item.get("value"))

    # Prefer Rotten Tomatoes critic rating when present, because it maps cleanly to Jellyfin CriticRating.
    for item in ratings:
        if isinstance(item, dict) and str(item.get("image") or "").startswith("rottentomatoes://"):
            value = rating_value(item)
            if value is not None:
                return value
    for item in ratings:
        value = rating_value(item)
        if value is not None:
            return value
    return None


def _xml_tags(parent: ET.Element, child_name: str) -> list[str]:
    values: list[str] = []
    for child in parent.findall(child_name):
        text = child.attrib.get("tag", "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _xml_child_dicts(parent: ET.Element, child_name: str) -> list[dict[str, object]]:
    return [dict(child.attrib) for child in parent.findall(child_name)]


def _xml_guid_ids(parent: ET.Element) -> list[str]:
    values: list[str] = []
    for child in parent.findall("Guid"):
        text = child.attrib.get("id", "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _dedupe_strings(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
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
