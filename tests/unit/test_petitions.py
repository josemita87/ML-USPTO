"""Tests for the petition-PDF gap detector in `ingest.petitions`."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.petitions import enumerate_missing_petition_pdfs
from ml_uspto.ingest.schemas.enums import Stage


def _petitions_frame() -> pd.DataFrame:
    """Five petition rows covering each filter layer."""
    return pd.DataFrame(
        [
            {
                "trial_number": "IPR2022-NEEDS_PDF",
                "petition_pdf_uri": "https://example/needs.pdf",
                "petition_filing_date_doc": date(2022, 5, 23),
            },
            {
                "trial_number": "IPR2022-CACHED",
                "petition_pdf_uri": "https://example/cached.pdf",
                "petition_filing_date_doc": date(2022, 6, 1),
            },
            {
                "trial_number": "IPR2022-NO_URI_NONE",
                "petition_pdf_uri": None,
                "petition_filing_date_doc": date(2022, 7, 1),
            },
            {
                "trial_number": "IPR2022-NO_URI_BLANK",
                "petition_pdf_uri": "   ",
                "petition_filing_date_doc": date(2022, 7, 15),
            },
            {
                "trial_number": "IPR2022-FRESH",
                "petition_pdf_uri": "https://example/fresh.pdf",
                "petition_filing_date_doc": date(2022, 8, 1),
            },
        ]
    )


@pytest.fixture
def seeded_storage(tmp_path: Path) -> LocalStorage:
    """LocalStorage with one cached petition-text blob (IPR2022-CACHED)."""
    storage = LocalStorage(
        raw_root=tmp_path / "raw", processed_root=tmp_path / "processed"
    )
    storage.save_blob(Stage.PETITION_TEXTS, "IPR2022-CACHED", "txt", b"cached")
    return storage


def test_keeps_only_uncached_with_uri(seeded_storage):
    """Cached + missing-URI rows are excluded; only fresh-with-URI survive."""
    out = enumerate_missing_petition_pdfs(seeded_storage, petitions=_petitions_frame())
    assert set(out["trial_number"]) == {"IPR2022-NEEDS_PDF", "IPR2022-FRESH"}


def test_returns_typed_empty_on_empty_input(tmp_path):
    """Empty petitions frame yields a typed empty result, not an error."""
    storage = LocalStorage(
        raw_root=tmp_path / "raw", processed_root=tmp_path / "processed"
    )
    out = enumerate_missing_petition_pdfs(storage, petitions=pd.DataFrame())
    assert out.empty
    assert list(out.columns) == [
        "trial_number",
        "petition_pdf_uri",
        "petition_filing_date_doc",
    ]


def test_all_cached_returns_empty(tmp_path):
    """When every petition is already cached, the candidate frame is empty."""
    storage = LocalStorage(
        raw_root=tmp_path / "raw", processed_root=tmp_path / "processed"
    )
    petitions = _petitions_frame()
    for trial in petitions["trial_number"]:
        storage.save_blob(Stage.PETITION_TEXTS, str(trial), "txt", b"x")
    out = enumerate_missing_petition_pdfs(storage, petitions=petitions)
    assert out.empty


def test_drops_blank_and_none_uri_rows(tmp_path):
    """Both `None` and whitespace-only URIs are filtered out."""
    storage = LocalStorage(
        raw_root=tmp_path / "raw", processed_root=tmp_path / "processed"
    )
    petitions = pd.DataFrame(
        [
            {
                "trial_number": "T1",
                "petition_pdf_uri": None,
                "petition_filing_date_doc": date(2022, 1, 1),
            },
            {
                "trial_number": "T2",
                "petition_pdf_uri": "  ",
                "petition_filing_date_doc": date(2022, 2, 1),
            },
        ]
    )
    out = enumerate_missing_petition_pdfs(storage, petitions=petitions)
    assert out.empty
