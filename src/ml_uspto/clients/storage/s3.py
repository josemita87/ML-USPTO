"""S3 storage backend mirroring `clients.local.LocalStorage`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import boto3
import pandas as pd
from botocore.exceptions import ClientError

from ml_uspto.schemas.constants import S3_NOT_FOUND_CODES
from ml_uspto.schemas.enums import Frame


def _json_default(obj: Any) -> str:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj).__name__}")


class S3Storage:
    """`Storage` Protocol implementation against S3.

    The local backend splits state across two filesystem roots (`raw_root`,
    `processed_root`); this backend collapses both into a single bucket
    under two key prefixes (`raw/`, `processed/` by default), so keys are
    byte-identical to `LocalStorage` paths and `aws s3 sync data/raw/
    s3://bucket/raw/` is a valid bootstrap seed. Frame parquet round-trips
    through `BytesIO` rather than via `s3fs` to keep the dependency
    surface to plain `boto3`.
    """

    def __init__(
        self,
        *,
        bucket: str,
        raw_prefix: str = "raw/",
        processed_prefix: str = "processed/",
        client: Any = None,
    ) -> None:
        """Bind a bucket + prefixes; defaults a `boto3` client when none given."""
        if not bucket:
            raise ValueError("bucket is required")
        self._bucket = bucket
        self._raw_prefix = raw_prefix if raw_prefix.endswith("/") else raw_prefix + "/"
        self._processed_prefix = (
            processed_prefix if processed_prefix.endswith("/") else processed_prefix + "/"
        )
        self._client = client if client is not None else boto3.client("s3")

    def _frame_key(self, key: Frame) -> str:
        return f"{self._processed_prefix}{key.value}.parquet"

    def _object_key(self, bucket: str, key: str) -> str:
        return f"{self._raw_prefix}{bucket}/{key}.json"

    def _blob_key(self, bucket: str, key: str, ext: str) -> str:
        return f"{self._raw_prefix}{bucket}/{key}.{ext}"

    def load_frame(
        self, key: Frame, *, columns: list[str] | None = None
    ) -> pd.DataFrame:
        """Read a `Frame` parquet from S3 via in-memory bytes."""
        obj = self._client.get_object(Bucket=self._bucket, Key=self._frame_key(key))
        return pd.read_parquet(BytesIO(obj["Body"].read()), columns=columns)

    def save_frame(self, df: pd.DataFrame, key: Frame) -> None:
        """Serialize `df` to parquet bytes and PUT under the frame key."""
        buf = BytesIO()
        df.to_parquet(buf, index=False)
        self._client.put_object(
            Bucket=self._bucket, Key=self._frame_key(key), Body=buf.getvalue()
        )

    def load_object(self, bucket: str, key: str) -> dict[str, Any] | None:
        """Return the JSON object at `<raw>/<bucket>/<key>.json`, or None if 404."""
        try:
            obj = self._client.get_object(
                Bucket=self._bucket, Key=self._object_key(bucket, key)
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in S3_NOT_FOUND_CODES:
                return None
            raise
        return json.loads(obj["Body"].read())

    def save_object(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        """Encode `payload` as JSON and PUT under the object key."""
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket, Key=self._object_key(bucket, key), Body=body
        )

    def iter_objects(self, bucket: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield `(key, payload)` for every JSON object under the bucket prefix."""
        prefix = f"{self._raw_prefix}{bucket}/"
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for entry in page.get("Contents") or []:
                k = entry["Key"]
                if not k.endswith(".json"):
                    continue
                stem = k[len(prefix):-len(".json")]
                obj = self._client.get_object(Bucket=self._bucket, Key=k)
                yield stem, json.loads(obj["Body"].read())

    def has_blob(self, bucket: str, key: str, ext: str) -> bool:
        """Return True if a blob exists, swallowing 404s as False."""
        try:
            self._client.head_object(
                Bucket=self._bucket, Key=self._blob_key(bucket, key, ext)
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in S3_NOT_FOUND_CODES:
                return False
            raise
        return True

    def save_blob(self, bucket: str, key: str, ext: str, payload: bytes) -> None:
        """PUT raw bytes at the blob key."""
        self._client.put_object(
            Bucket=self._bucket, Key=self._blob_key(bucket, key, ext), Body=payload
        )

    def load_blob(self, bucket: str, key: str, ext: str) -> bytes | None:
        """Return blob bytes, or None if the key is missing."""
        try:
            obj = self._client.get_object(
                Bucket=self._bucket, Key=self._blob_key(bucket, key, ext)
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in S3_NOT_FOUND_CODES:
                return None
            raise
        return obj["Body"].read()

    def iter_blob_keys(self, bucket: str, ext: str) -> Iterator[str]:
        """Yield blob stems for every `*.<ext>` under the bucket prefix."""
        prefix = f"{self._raw_prefix}{bucket}/"
        suffix = f".{ext}"
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for entry in page.get("Contents") or []:
                k = entry["Key"]
                if k.endswith(suffix):
                    yield k[len(prefix):-len(suffix)]


__all__ = ["S3Storage"]
