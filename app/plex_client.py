from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class PlexClient:
    base_url: str
    token: str
    timeout: int = 15

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
            dirs = data.findall("Directory")
            for item in dirs:
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
