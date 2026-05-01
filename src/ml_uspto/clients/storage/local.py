"""Local filesystem storage backend."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ml_uspto import paths
from ml_uspto.schemas.enums import Frame


def _json_default(obj: Any) -> str:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj).__name__}")


class LocalStorage:
    """Default backend — reads/writes under the project's local data dirs.

    Implements `storage.Storage` against the local filesystem with two
    roots: `processed_root` for `Frame` parquets
    (`<root>/<frame>.parquet`) and `raw_root` for JSON object buckets
    (`<root>/<bucket>/<key>.json`). The split mirrors the project's
    data layout (raw cache vs processed outputs). An S3 backend can
    collapse both into a single bucket with prefixes.

    `raw_root` and `processed_root` default to `paths.raw_dir()` and
    `paths.processed_dir()` (settings-driven). Tests pass
    `tmp_path`-rooted instances to isolate I/O.
    """

    def __init__(
        self,
        *,
        raw_root: Path | None = None,
        processed_root: Path | None = None,
    ) -> None:
        """Stash overrides; resolve to `paths.*_dir()` lazily on access."""
        self._raw_root = raw_root
        self._processed_root = processed_root

    @property
    def raw_root(self) -> Path:
        """Raw object cache root (defaults to `paths.raw_dir()`)."""
        return self._raw_root if self._raw_root is not None else paths.raw_dir()

    @property
    def processed_root(self) -> Path:
        """Processed-frame root (defaults to `paths.processed_dir()`)."""
        return self._processed_root if self._processed_root is not None else paths.processed_dir()

    def load_frame(
        self, key: Frame, *, columns: list[str] | None = None
    ) -> pd.DataFrame:
        """Read a `Frame` parquet from `processed_root`."""
        return pd.read_parquet(self.processed_root / f"{key.value}.parquet", columns=columns)

    def save_frame(self, df: pd.DataFrame, key: Frame) -> None:
        """Write `df` as parquet under `processed_root/<frame>.parquet`."""
        path = self.processed_root / f"{key.value}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)

    def load_object(self, bucket: str, key: str) -> dict[str, Any] | None:
        """Return the JSON payload at `<bucket>/<key>.json`, or None if missing."""
        path = self.raw_root / bucket / f"{key}.json"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save_object(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        """Write `payload` as JSON under `<bucket>/<key>.json`."""
        path = self.raw_root / bucket / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, default=_json_default)

    def iter_objects(self, bucket: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield `(key, payload)` for every JSON object in `bucket`, sorted by key."""
        base = self.raw_root / bucket
        if not base.exists():
            return
        for path in sorted(base.glob("*.json")):
            with path.open("r", encoding="utf-8") as f:
                yield path.stem, json.load(f)

    def blob_path(self, bucket: str, key: str, ext: str) -> Path:
        """Resolve the on-disk path for a binary blob (no I/O)."""
        return self.raw_root / bucket / f"{key}.{ext}"

    def has_blob(self, bucket: str, key: str, ext: str) -> bool:
        """Return True if the blob exists on disk."""
        return self.blob_path(bucket, key, ext).exists()

    def save_blob(self, bucket: str, key: str, ext: str, payload: bytes) -> None:
        """Write raw bytes to the blob path, creating parents as needed."""
        path = self.blob_path(bucket, key, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def load_blob(self, bucket: str, key: str, ext: str) -> bytes | None:
        """Return the blob bytes, or None if absent."""
        path = self.blob_path(bucket, key, ext)
        if not path.exists():
            return None
        return path.read_bytes()

    def iter_blob_keys(self, bucket: str, ext: str) -> Iterator[str]:
        """Yield blob stems for every `*.<ext>` under `bucket`."""
        base = self.raw_root / bucket
        if not base.exists():
            return
        for path in base.glob(f"*.{ext}"):
            yield path.stem


__all__ = ["LocalStorage"]
