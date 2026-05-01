"""Tests for `ingest.fetch.fetch_patents` request batching and miss handling."""
from pathlib import Path

import requests

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.fetch import fetch_patents
from ml_uspto.ingest.schemas.enums import Stage


class FakeUSPTOClient:
    """Stub USPTOClient that records requested application numbers."""

    def __init__(self, records=None, error=None):
        """Seed the stub with optional canned records and an optional error to raise."""
        self.records = records or {}
        self.error = error
        self.calls = []

    def search_applications_post(self, *, filters, offset, limit):
        """Return canned records for the filtered application numbers, or raise."""
        values = filters[0]["value"]
        self.calls.append(list(values))
        if self.error is not None:
            raise self.error
        return {
            "count": len([app for app in values if app in self.records]),
            "patentFileWrapperDataBag": [
                {"applicationNumberText": app, **self.records[app]}
                for app in values
                if app in self.records
            ],
        }


def _storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")


def test_fetch_patents_dedups_and_extracts_wrapper_record(tmp_path: Path):
    """Duplicate application numbers collapse to one search call and one result."""
    storage = _storage(tmp_path)
    client = FakeUSPTOClient({"14709428": {"applicationMetaData": {"filingDate": "2015-05-11"}}})

    results = list(fetch_patents(storage, client, ["14709428", "14709428"], page_size=100))

    assert client.calls == [["14709428"]]
    assert len(results) == 1
    assert results[0].application_number == "14709428"
    assert results[0].raw_record == {
        "applicationMetaData": {"filingDate": "2015-05-11"},
        "applicationNumberText": "14709428",
    }


def test_fetch_patents_missing_search_result_yields_empty_and_retries_next_run(tmp_path: Path):
    """Misses are not cached so the next run re-queries the API."""
    storage = _storage(tmp_path)
    client = FakeUSPTOClient()

    first = list(fetch_patents(storage, client, ["00000000"], page_size=100))
    second = list(fetch_patents(storage, client, ["00000000"], page_size=100))

    # Both runs hit the API — we don't cache misses, we let the next cron retry.
    assert client.calls == [["00000000"], ["00000000"]]
    assert first[0].raw_record is None
    assert second[0].raw_record is None
    # No stub left in raw cache.
    assert storage.load_object(Stage.PATENTS, "00000000") is None


def test_fetch_patents_http_error_yields_empty_for_each_app(tmp_path: Path):
    """A failed batch produces one empty result per requested application."""
    storage = _storage(tmp_path)
    response = requests.Response()
    response.status_code = 503
    client = FakeUSPTOClient(error=requests.HTTPError("unavailable", response=response))

    results = list(fetch_patents(storage, client, ["1", "2"], page_size=100))

    assert client.calls == [["1", "2"]]
    assert [r.raw_record for r in results] == [None, None]
