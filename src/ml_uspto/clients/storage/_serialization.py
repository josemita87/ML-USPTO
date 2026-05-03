"""Shared serialization helpers for the storage backends."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any


def json_default(obj: Any) -> str:
    """`default=` hook for `json.dumps` covering the project's non-JSON types.

    Both `LocalStorage` and `S3Storage` write JSON payloads that may
    contain dates (label/decision rows) and Path objects (manifest
    audits); routing them through this single hook keeps the two
    backends byte-identical on disk.
    """
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj).__name__}")
