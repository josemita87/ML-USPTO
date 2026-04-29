import requests

from ml_uspto.ingest.fetch import fetch_patents
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.schemas.enums import PatentQuarantineReason


class FakeUSPTOClient:
    def __init__(self, records=None, error=None):
        self.records = records or {}
        self.error = error
        self.calls = []

    def search_applications_post(self, *, filters, offset, limit):
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


def test_fetch_patents_dedups_and_extracts_wrapper_record(monkeypatch):
    saved = {}

    def load_if_present(bucket, key):
        assert bucket is Stage.PATENTS
        return saved.get(key)

    def save(bucket, key, payload):
        assert bucket is Stage.PATENTS
        saved[key] = payload

    monkeypatch.setattr("ml_uspto.ingest.fetch.local.load_if_present", load_if_present)
    monkeypatch.setattr("ml_uspto.ingest.fetch.local.save", save)

    client = FakeUSPTOClient({"14709428": {"applicationMetaData": {"filingDate": "2015-05-11"}}})

    results = list(fetch_patents(client, ["14709428", "14709428"], page_size=100))

    assert client.calls == [["14709428"]]
    assert len(results) == 1
    assert results[0].application_number == "14709428"
    assert results[0].raw_record == {
        "applicationMetaData": {"filingDate": "2015-05-11"},
        "applicationNumberText": "14709428",
    }
    assert results[0].quarantine is None


def test_fetch_patents_caches_missing_search_result(monkeypatch):
    saved = {}

    def load_if_present(bucket, key):
        assert bucket is Stage.PATENTS
        return saved.get(key)

    def save(bucket, key, payload):
        assert bucket is Stage.PATENTS
        saved[key] = payload

    monkeypatch.setattr("ml_uspto.ingest.fetch.local.load_if_present", load_if_present)
    monkeypatch.setattr("ml_uspto.ingest.fetch.local.save", save)

    client = FakeUSPTOClient()

    first = list(fetch_patents(client, ["00000000"], page_size=100))
    second = list(fetch_patents(client, ["00000000"], page_size=100))

    assert client.calls == [["00000000"]]
    assert first[0].quarantine is not None
    assert first[0].quarantine.reason is PatentQuarantineReason.NOT_FOUND
    assert second[0].quarantine is not None
    assert second[0].quarantine.reason is PatentQuarantineReason.NOT_FOUND


def test_fetch_patents_quarantines_batch_http_error(monkeypatch):
    saved = {}

    def load_if_present(bucket, key):
        assert bucket is Stage.PATENTS
        return saved.get(key)

    def save(bucket, key, payload):
        assert bucket is Stage.PATENTS
        saved[key] = payload

    monkeypatch.setattr("ml_uspto.ingest.fetch.local.load_if_present", load_if_present)
    monkeypatch.setattr("ml_uspto.ingest.fetch.local.save", save)

    response = requests.Response()
    response.status_code = 503
    client = FakeUSPTOClient(error=requests.HTTPError("unavailable", response=response))

    results = list(fetch_patents(client, ["1", "2"], page_size=100))

    assert client.calls == [["1", "2"]]
    assert [r.quarantine.reason for r in results if r.quarantine] == [
        PatentQuarantineReason.HTTP_ERROR,
        PatentQuarantineReason.HTTP_ERROR,
    ]
