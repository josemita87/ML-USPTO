"""Backend-agnostic storage protocol.

`Storage` is the contract every backend implements. Two surfaces:

  - `load_frame` / `save_frame` — tabular outputs keyed by `Frame`. Frames
    live in a single project namespace (the "processed" outputs); there's
    no bucket because there's only one. Backend chooses the on-wire
    format (parquet for `LocalStorage`).
  - `load_object` / `save_object` / `iter_objects` — JSON-shaped object
    cache keyed by `(bucket, key)`. Buckets are pipeline `Stage` values;
    keys are opaque (page numbers, application numbers, …).

Concrete implementations live under `clients/` (`clients/local.py`,
`clients/s3.py`). Callers depend on the Protocol, not on a class.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

import pandas as pd

from ml_uspto.schemas.enums import Frame


class Storage(Protocol):
    """Backend-agnostic storage contract; concrete impls live under `clients/`."""

    def load_frame(self, key: Frame, *, columns: list[str] | None = None) -> pd.DataFrame:
        """Load the tabular output keyed by `key` (optionally projecting columns)."""
        ...

    def save_frame(self, df: pd.DataFrame, key: Frame) -> None:
        """Persist `df` as the tabular output for `key` (overwrites)."""
        ...

    def load_object(self, bucket: str, key: str) -> dict[str, Any] | None:
        """Load JSON object at `(bucket, key)`; return None if absent."""
        ...

    def save_object(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        """Persist `payload` as JSON at `(bucket, key)`."""
        ...

    def iter_objects(self, bucket: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield `(key, payload)` for every JSON object in `bucket`."""
        ...

    def load_blob(self, bucket: str, key: str, ext: str) -> bytes | None:
        """Load raw bytes at `(bucket, key, ext)`; return None if absent."""
        ...

    def has_blob(self, bucket: str, key: str, ext: str) -> bool:
        """Return True iff a blob exists at `(bucket, key, ext)`."""
        ...

    def save_blob(self, bucket: str, key: str, ext: str, payload: bytes) -> None:
        """Persist raw `payload` at `(bucket, key, ext)`."""
        ...

    def iter_blob_keys(self, bucket: str, ext: str) -> Iterator[str]:
        """Yield the bare key (no extension) for every blob in `bucket` with `ext`."""
        ...


__all__ = ["Storage"]
