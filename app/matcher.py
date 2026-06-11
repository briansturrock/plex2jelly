from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalise_path(path: str) -> str:
    return (path or "").replace("\\", "/").rstrip("/")


def canonicalise_path(path: str, mappings: list[dict[str, str]], source: str) -> str:
    """Convert a Plex or Jellyfin path into a shared canonical path using mappings."""
    cleaned = normalise_path(path)
    source_key = "plex_path" if source == "plex" else "jellyfin_path"
    other_key = "jellyfin_path" if source == "plex" else "plex_path"

    for mapping in mappings:
        src = normalise_path(mapping.get(source_key, ""))
        dst = normalise_path(mapping.get(other_key, ""))
        if src and cleaned.startswith(src):
            suffix = cleaned[len(src):].lstrip("/")
            return "/" + suffix if suffix else "/"
        if dst and cleaned.startswith(dst):
            suffix = cleaned[len(dst):].lstrip("/")
            return "/" + suffix if suffix else "/"
    return cleaned


def compact_title(value: str) -> str:
    text = (value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_warning(plex_title: str, jellyfin_title: str) -> bool:
    left = compact_title(plex_title)
    right = compact_title(jellyfin_title)
    if left == right:
        return False
    return SequenceMatcher(None, left, right).ratio() < 0.88


def runtime_warning(plex_ms: int | None, jellyfin_ms: int | None) -> bool:
    if plex_ms is None or jellyfin_ms is None:
        return False
    return abs(plex_ms - jellyfin_ms) > 120000


def warning_reasons(row: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    if title_warning(str(row.get("plex_title") or ""), str(row.get("jellyfin_title") or "")):
        reasons.append("title differs")
    py = row.get("plex_year")
    jy = row.get("jellyfin_year")
    if py and jy and py != jy:
        reasons.append("year differs")
    if runtime_warning(row.get("plex_duration_ms"), row.get("jellyfin_duration_ms")):
        reasons.append("runtime differs")
    return reasons
