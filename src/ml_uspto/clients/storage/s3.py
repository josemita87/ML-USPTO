"""S3 storage backend.

Mirrors `clients.local.LocalStorage` against an S3 bucket. The local
backend splits state across two filesystem roots (`raw_root`,
`processed_root`); this backend collapses both into a single bucket with
two key prefixes (`raw/`, `processed/` by default) so an `aws s3 sync`
from the local layout lands at the right keys without transformation.

Frame parquet round-trips through `BytesIO` rather than via `s3fs`, to
keep the dependency surface to plain `boto3`. Object payloads use the
a small `_json_default` (date / datetime / Path), inlined here.
"""

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

    Keys are byte-identical to `LocalStorage` paths under the two prefixes,
    so the bootstrap seed is `aws s3 sync data/raw/ s3://bucket/raw/`.
    """

    def __init__(
        self,
        *,
        bucket: str,
        raw_prefix: str = "raw/",
        processed_prefix: str = "processed/",
        client: Any = None,
    ) -> None:
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
        obj = self._client.get_object(Bucket=self._bucket, Key=self._frame_key(key))
        return pd.read_parquet(BytesIO(obj["Body"].read()), columns=columns)

    def save_frame(self, df: pd.DataFrame, key: Frame) -> None:
        buf = BytesIO()
        df.to_parquet(buf, index=False)
        self._client.put_object(
            Bucket=self._bucket, Key=self._frame_key(key), Body=buf.getvalue()
        )

    def load_object(self, bucket: str, key: str) -> dict[str, Any] | None:
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
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket, Key=self._object_key(bucket, key), Body=body
        )

    def iter_objects(self, bucket: str) -> Iterator[tuple[str, dict[str, Any]]]:
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
        self._client.put_object(
            Bucket=self._bucket, Key=self._blob_key(bucket, key, ext), Body=payload
        )

    def load_blob(self, bucket: str, key: str, ext: str) -> bytes | None:
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
        prefix = f"{self._raw_prefix}{bucket}/"
        suffix = f".{ext}"
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for entry in page.get("Contents") or []:
                k = entry["Key"]
                if k.endswith(suffix):
                    yield k[len(prefix):-len(suffix)]


__all__ = ["S3Storage"]
