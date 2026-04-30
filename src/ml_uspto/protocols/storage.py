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
    def load_frame(self, key: Frame, *, columns: list[str] | None = None) -> pd.DataFrame: ...

    def save_frame(self, df: pd.DataFrame, key: Frame) -> None: ...

    def load_object(self, bucket: str, key: str) -> dict[str, Any] | None: ...

    def save_object(self, bucket: str, key: str, payload: dict[str, Any]) -> None: ...

    def iter_objects(self, bucket: str) -> Iterator[tuple[str, dict[str, Any]]]: ...

    def load_blob(self, bucket: str, key: str, ext: str) -> bytes | None: ...

    def has_blob(self, bucket: str, key: str, ext: str) -> bool: ...

    def save_blob(self, bucket: str, key: str, ext: str, payload: bytes) -> None: ...

    def iter_blob_keys(self, bucket: str, ext: str) -> Iterator[str]: ...


__all__ = ["Storage"]
