from __future__ import annotations


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
