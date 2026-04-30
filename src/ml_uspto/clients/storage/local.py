"""Local filesystem storage backend.

Implements `storage.Storage` against the local FS, with two roots:

  - `processed_root` for `Frame` parquets — `<root>/<frame>.parquet`
  - `raw_root` for object buckets — `<root>/<bucket>/<key>.json`

The split mirrors the project's data layout (raw cache vs processed
outputs). An S3 backend can collapse both into a single bucket with
prefixes; the Protocol contract is the same.
"""

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

    `raw_root` and `processed_root` default to `paths.raw_dir()` and
    `paths.processed_dir()` (settings-driven). Tests pass `tmp_path`-rooted
    instances to isolate I/O.
    """

    def __init__(
        self,
        *,
        raw_root: Path | None = None,
        processed_root: Path | None = None,
    ) -> None:
        self._raw_root = raw_root
        self._processed_root = processed_root

    @property
    def raw_root(self) -> Path:
        return self._raw_root if self._raw_root is not None else paths.raw_dir()

    @property
    def processed_root(self) -> Path:
        return self._processed_root if self._processed_root is not None else paths.processed_dir()

    def load_frame(
        self, key: Frame, *, columns: list[str] | None = None
    ) -> pd.DataFrame:
        return pd.read_parquet(self.processed_root / f"{key.value}.parquet", columns=columns)

    def save_frame(self, df: pd.DataFrame, key: Frame) -> None:
        path = self.processed_root / f"{key.value}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)

    def load_object(self, bucket: str, key: str) -> dict[str, Any] | None:
        path = self.raw_root / bucket / f"{key}.json"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def save_object(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        path = self.raw_root / bucket / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, default=_json_default)

    def iter_objects(self, bucket: str) -> Iterator[tuple[str, dict[str, Any]]]:
        base = self.raw_root / bucket
        if not base.exists():
            return
        for path in sorted(base.glob("*.json")):
            with path.open("r", encoding="utf-8") as f:
                yield path.stem, json.load(f)

    def blob_path(self, bucket: str, key: str, ext: str) -> Path:
        return self.raw_root / bucket / f"{key}.{ext}"

    def has_blob(self, bucket: str, key: str, ext: str) -> bool:
        return self.blob_path(bucket, key, ext).exists()

    def save_blob(self, bucket: str, key: str, ext: str, payload: bytes) -> None:
        path = self.blob_path(bucket, key, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def load_blob(self, bucket: str, key: str, ext: str) -> bytes | None:
        path = self.blob_path(bucket, key, ext)
        if not path.exists():
            return None
        return path.read_bytes()

    def iter_blob_keys(self, bucket: str, ext: str) -> Iterator[str]:
        base = self.raw_root / bucket
        if not base.exists():
            return
        for path in base.glob(f"*.{ext}"):
            yield path.stem


__all__ = ["LocalStorage"]
