"""Round-trip + idempotency tests for `LocalStorage`."""

from pathlib import Path

import pandas as pd

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.schemas.enums import Frame


def _storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")


def test_object_round_trip(tmp_path: Path):
    storage = _storage(tmp_path)
    payload = {"trialNumber": "IPR2026-00001", "rows": [{"a": 1}]}
    storage.save_object(Stage.PROCEEDINGS, "page_0", payload)

    assert storage.load_object(Stage.PROCEEDINGS, "page_0") == payload


def test_load_object_returns_none_when_missing(tmp_path: Path):
    storage = _storage(tmp_path)
    assert storage.load_object(Stage.PROCEEDINGS, "missing") is None


def test_object_path_layout(tmp_path: Path):
    storage = _storage(tmp_path)
    storage.save_object(Stage.DOCUMENTS_PETITION_SCAN, "page_42", {"v": 1})

    assert (tmp_path / "raw" / "documents_petition_scan" / "page_42.json").exists()


def test_iter_objects_yields_in_sorted_order(tmp_path: Path):
    storage = _storage(tmp_path)
    storage.save_object(Stage.PATENTS, "20000003", {"app": 3})
    storage.save_object(Stage.PATENTS, "20000001", {"app": 1})
    storage.save_object(Stage.PATENTS, "20000002", {"app": 2})

    items = list(storage.iter_objects(Stage.PATENTS))
    assert [k for k, _ in items] == ["20000001", "20000002", "20000003"]
    assert items[0][1] == {"app": 1}


def test_iter_objects_empty_when_bucket_dir_missing(tmp_path: Path):
    storage = _storage(tmp_path)
    assert list(storage.iter_objects(Stage.PATENTS)) == []


def test_save_object_overwrites_existing(tmp_path: Path):
    storage = _storage(tmp_path)
    storage.save_object(Stage.PROCEEDINGS, "k", {"v": 1})
    storage.save_object(Stage.PROCEEDINGS, "k", {"v": 2})

    assert storage.load_object(Stage.PROCEEDINGS, "k") == {"v": 2}


def test_save_object_creates_parent_directories(tmp_path: Path):
    nested = tmp_path / "does" / "not" / "exist"
    storage = LocalStorage(raw_root=nested, processed_root=tmp_path / "processed")
    storage.save_object(Stage.PROCEEDINGS, "k", {"v": 1})

    assert (nested / "proceedings" / "k.json").exists()


def test_frame_round_trip(tmp_path: Path):
    storage = _storage(tmp_path)
    df = pd.DataFrame({"trial_number": ["IPR2026-00001"], "n": [3]})
    storage.save_frame(df, Frame.TRIALS)

    loaded = storage.load_frame(Frame.TRIALS)
    pd.testing.assert_frame_equal(loaded, df)


def test_frame_columns_projection(tmp_path: Path):
    storage = _storage(tmp_path)
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    storage.save_frame(df, Frame.TRIALS)

    loaded = storage.load_frame(Frame.TRIALS, columns=["a"])
    assert list(loaded.columns) == ["a"]
