"""Round-trip + idempotency tests for the local-FS object-store local."""

from pathlib import Path

from ml_uspto.clients import local
from ml_uspto.ingest.schemas.enums import Stage


def test_save_and_load_round_trip(tmp_path: Path):
    payload = {"trialNumber": "IPR2026-00001", "rows": [{"a": 1}]}
    local.save(Stage.PROCEEDINGS, "page_0", payload, root=tmp_path)

    loaded = local.load_if_present(Stage.PROCEEDINGS, "page_0", root=tmp_path)
    assert loaded == payload


def test_load_if_present_returns_none_when_missing(tmp_path: Path):
    assert local.load_if_present(Stage.PROCEEDINGS, "missing", root=tmp_path) is None


def test_cache_path_includes_stage(tmp_path: Path):
    p = local.cache_path(Stage.DOCUMENTS_PETITION_SCAN, "page_42", root=tmp_path)
    assert p == tmp_path / "documents_petition_scan" / "page_42.json"


def test_iter_cached_yields_in_sorted_order(tmp_path: Path):
    local.save(Stage.PATENTS, "20000003", {"app": 3}, root=tmp_path)
    local.save(Stage.PATENTS, "20000001", {"app": 1}, root=tmp_path)
    local.save(Stage.PATENTS, "20000002", {"app": 2}, root=tmp_path)

    items = list(local.iter_cached(Stage.PATENTS, root=tmp_path))
    assert [k for k, _ in items] == ["20000001", "20000002", "20000003"]
    assert items[0][1] == {"app": 1}


def test_iter_cached_empty_when_stage_dir_missing(tmp_path: Path):
    assert list(local.iter_cached(Stage.PATENTS, root=tmp_path)) == []


def test_save_overwrites_existing(tmp_path: Path):
    local.save(Stage.PROCEEDINGS, "k", {"v": 1}, root=tmp_path)
    local.save(Stage.PROCEEDINGS, "k", {"v": 2}, root=tmp_path)

    assert local.load_if_present(Stage.PROCEEDINGS, "k", root=tmp_path) == {"v": 2}


def test_save_creates_parent_directories(tmp_path: Path):
    nested_root = tmp_path / "does" / "not" / "exist"
    local.save(Stage.PROCEEDINGS, "k", {"v": 1}, root=nested_root)

    assert (nested_root / "proceedings" / "k.json").exists()
