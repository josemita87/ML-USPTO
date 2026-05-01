"""Round-trip + idempotency tests for `S3Storage` against moto-mocked S3.

Mirrors `test_local.py` so any divergence between the two backends shows
up as a test gap. Uses `moto`'s `mock_aws` to keep tests offline.
"""

from __future__ import annotations

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from ml_uspto.clients.storage.s3 import S3Storage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.schemas.enums import Frame

BUCKET = "test-bucket"


@pytest.fixture
def storage():
    """Yield an S3Storage instance backed by moto."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3Storage(bucket=BUCKET, client=client)


def test_object_round_trip(storage: S3Storage):
    """Persist and reload a raw JSON object."""
    payload = {"trialNumber": "IPR2026-00001", "rows": [{"a": 1}]}
    storage.save_object(Stage.PROCEEDINGS, "page_0", payload)
    assert storage.load_object(Stage.PROCEEDINGS, "page_0") == payload


def test_load_object_returns_none_when_missing(storage: S3Storage):
    """Return None for missing raw objects."""
    assert storage.load_object(Stage.PROCEEDINGS, "missing") is None


def test_save_object_overwrites_existing(storage: S3Storage):
    """Overwrite an existing raw-object key."""
    storage.save_object(Stage.PROCEEDINGS, "k", {"v": 1})
    storage.save_object(Stage.PROCEEDINGS, "k", {"v": 2})
    assert storage.load_object(Stage.PROCEEDINGS, "k") == {"v": 2}


def test_iter_objects_yields_all_keys(storage: S3Storage):
    """Iterate every raw object key in the requested stage."""
    storage.save_object(Stage.PATENTS, "20000003", {"app": 3})
    storage.save_object(Stage.PATENTS, "20000001", {"app": 1})
    storage.save_object(Stage.PATENTS, "20000002", {"app": 2})
    items = sorted(storage.iter_objects(Stage.PATENTS))
    assert [k for k, _ in items] == ["20000001", "20000002", "20000003"]
    assert items[0][1] == {"app": 1}


def test_iter_objects_empty_when_bucket_dir_missing(storage: S3Storage):
    """Yield no objects when the stage prefix does not exist."""
    assert list(storage.iter_objects(Stage.PATENTS)) == []


def test_blob_round_trip_and_has_blob(storage: S3Storage):
    """Persist a raw blob and report its existence."""
    assert storage.has_blob(Stage.DECISION_TEXTS, "doc1", "txt") is False
    storage.save_blob(Stage.DECISION_TEXTS, "doc1", "txt", b"%PDF-1.4 test")
    assert storage.has_blob(Stage.DECISION_TEXTS, "doc1", "txt") is True
    assert storage.load_blob(Stage.DECISION_TEXTS, "doc1", "txt") == b"%PDF-1.4 test"


def test_load_blob_returns_none_when_missing(storage: S3Storage):
    """Return None for a missing raw blob."""
    assert storage.load_blob(Stage.DECISION_TEXTS, "missing", "txt") is None


def test_iter_blob_keys_skips_other_extensions(storage: S3Storage):
    """List only blob keys with the requested extension."""
    storage.save_blob(Stage.DECISION_TEXTS, "a", "txt", b"x")
    storage.save_blob(Stage.DECISION_TEXTS, "b", "txt", b"x")
    storage.save_object(Stage.DECISION_TEXTS, "c", {"meta": True})
    keys = sorted(storage.iter_blob_keys(Stage.DECISION_TEXTS, "txt"))
    assert keys == ["a", "b"]


def test_frame_round_trip(storage: S3Storage):
    """Persist and reload a processed parquet frame."""
    df = pd.DataFrame({"trial_number": ["IPR2026-00001"], "n": [3]})
    storage.save_frame(df, Frame.TRIALS)
    loaded = storage.load_frame(Frame.TRIALS)
    pd.testing.assert_frame_equal(loaded, df)


def test_frame_columns_projection(storage: S3Storage):
    """Load only requested columns from a processed frame."""
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    storage.save_frame(df, Frame.TRIALS)
    loaded = storage.load_frame(Frame.TRIALS, columns=["a"])
    assert list(loaded.columns) == ["a"]


def test_key_layout_matches_local_storage_paths(storage: S3Storage):
    """Seed-upload invariant: S3 keys must match local FS layout exactly."""
    storage.save_object(Stage.DOCUMENTS_PETITION_SCAN, "page_42", {"v": 1})
    storage.save_blob(Stage.DECISION_TEXTS, "171252146", "txt", b"x")
    storage.save_frame(pd.DataFrame({"a": [1]}), Frame.TRIALS)

    listed = sorted(
        e["Key"]
        for e in storage._client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    )
    assert listed == [
        "processed/trials.parquet",
        "raw/decision_texts/171252146.txt",
        "raw/documents_petition_scan/page_42.json",
    ]


def test_bucket_required():
    """Reject empty S3 bucket names."""
    with pytest.raises(ValueError):
        S3Storage(bucket="")
