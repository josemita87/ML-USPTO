"""Tests for `ingest.fetch.fetch_petition_pdfs` — download + extract + cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pdfplumber
import pytest
import requests

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.fetch import fetch_petition_pdfs
from ml_uspto.ingest.schemas.enums import Stage


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePdf:
    """Minimal pdfplumber-PDF stand-in for the fetch test."""

    def __init__(self, page_texts: list[str]) -> None:
        self.pages = [_FakePage(t) for t in page_texts]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(
        raw_root=tmp_path / "raw", processed_root=tmp_path / "processed"
    )


def _candidates(*pairs: tuple[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"trial_number": t, "petition_pdf_uri": u} for t, u in pairs]
    )


def test_persists_text(storage, monkeypatch):
    """Successful fetch caches the extracted text blob and yields bytes_written."""
    fake_pdf = _FakePdf(["Page one body.", "Page two body."])
    monkeypatch.setattr(pdfplumber, "open", lambda _bio: fake_pdf)

    client = MagicMock()
    client.download_pdf.return_value = b"%PDF-1.4 fake bytes"

    candidates = _candidates(("IPR2022-A", "https://example/a.pdf"))
    results = list(fetch_petition_pdfs(storage, client, candidates))

    assert len(results) == 1
    res = results[0]
    assert res.trial_number == "IPR2022-A"
    assert res.bytes_written is not None and res.bytes_written > 0

    cached = storage.load_blob(Stage.PETITION_TEXTS, "IPR2022-A", "txt")
    assert cached is not None
    assert cached.decode("utf-8") == "Page one body.\nPage two body."


def test_http_error_yields_failure_no_cache(storage, monkeypatch):
    """HTTP errors yield a result with bytes_written=None and write nothing."""
    client = MagicMock()
    client.download_pdf.side_effect = requests.HTTPError(
        "500 Server Error", response=MagicMock(status_code=500)
    )

    candidates = _candidates(("IPR2022-FAIL", "https://example/fail.pdf"))
    results = list(fetch_petition_pdfs(storage, client, candidates))

    assert len(results) == 1
    assert results[0].bytes_written is None
    assert storage.load_blob(Stage.PETITION_TEXTS, "IPR2022-FAIL", "txt") is None


def test_pdfplumber_error_yields_failure_no_cache(storage, monkeypatch):
    """A pdfplumber crash on parse yields failure but does not blow up the loop."""
    def _raise(_bio):
        raise RuntimeError("malformed pdf")

    monkeypatch.setattr(pdfplumber, "open", _raise)
    client = MagicMock()
    client.download_pdf.return_value = b"not actually a pdf"

    candidates = _candidates(("IPR2022-MALFORMED", "https://example/x.pdf"))
    results = list(fetch_petition_pdfs(storage, client, candidates))

    assert len(results) == 1
    assert results[0].bytes_written is None
    assert storage.load_blob(Stage.PETITION_TEXTS, "IPR2022-MALFORMED", "txt") is None


def test_empty_payload_yields_failure(storage, monkeypatch):
    """Zero-byte response is treated as failure."""
    client = MagicMock()
    client.download_pdf.return_value = b""

    candidates = _candidates(("IPR2022-EMPTY", "https://example/empty.pdf"))
    results = list(fetch_petition_pdfs(storage, client, candidates))

    assert len(results) == 1
    assert results[0].bytes_written is None
