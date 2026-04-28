"""Local-FS client — file I/O primitives + cache-shaped key/value access.

Two related roles in one module so the local-FS backend lives in one
place, parallel to the future `clients/s3.py`:

1. **I/O primitives** — `save_parquet`/`load_parquet`,
   `save_json`/`load_json`. Used anywhere that needs to read or write a
   file. Path resolution lives in `ml_uspto.paths`.

2. **Object-store cache** — `cache_path`, `load_if_present`, `save`,
   `iter_cached`. Bucket/key/payload model that mirrors S3, so the same
   key shape maps 1:1 (`s3://<bucket>/raw/<bucket>/<key>.json`) when we
   swap backends per
   `docs/plans/2026-04-27-ingestion-pipeline.md` §4.2.

Bucket strings come from `ingest.schemas.enums.Stage` (StrEnum, str
subclass) — callers pass `Stage.PROCEEDINGS` directly; the cache treats
it as opaque text so the client stays free of pipeline-stage knowledge.
"""

import json
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ml_uspto import paths

# ---------------------------------------------------------------------------
# I/O primitives
# ---------------------------------------------------------------------------


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def load_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def save_json(payload: dict, path: Path) -> None:
    """Write `payload` as UTF-8 JSON, creating parents as needed.

    Dates and Paths are stringified via `_json_default` so a round-trip with
    `load_json` preserves shape (dates come back as ISO strings, not `date`).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, default=_json_default)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _json_default(obj: Any) -> str:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj).__name__}")


# ---------------------------------------------------------------------------
# Object-store cache (bucket/key/payload, mirrors S3)
# ---------------------------------------------------------------------------


def _root(root: Path | None) -> Path:
    if root is not None:
        return root
    return paths.raw_dir()


def cache_path(bucket: str, key: str, *, root: Path | None = None) -> Path:
    return _root(root) / bucket / f"{key}.json"


def load_if_present(
    bucket: str, key: str, *, root: Path | None = None
) -> dict | None:
    path = cache_path(bucket, key, root=root)
    if not path.exists():
        return None
    return load_json(path)


def save(
    bucket: str, key: str, payload: dict, *, root: Path | None = None
) -> None:
    save_json(payload, cache_path(bucket, key, root=root))


def iter_cached(
    bucket: str, *, root: Path | None = None
) -> Iterator[tuple[str, dict]]:
    """Yield `(key, payload)` for every cached entry, sorted by key."""
    base = _root(root) / bucket
    if not base.exists():
        return
    for path in sorted(base.glob("*.json")):
        yield path.stem, load_json(path)
