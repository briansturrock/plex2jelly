from __future__ import annotations


def normalise_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")
