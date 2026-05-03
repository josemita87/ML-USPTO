"""Tests for the FWD-PDF gap detector in `ingest.decisions`."""
from pathlib import Path

import pandas as pd
import pytest

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.decisions import enumerate_missing_fwd_pdfs
from ml_uspto.ingest.schemas.enums import Stage


def _decisions_frame() -> pd.DataFrame:
    """Five FWD-shaped flattened decisions rows covering each filter layer."""
    return pd.DataFrame(
        [
            # 1. title-resolvable → excluded by title regex
            {
                "trial_number": "IPR2022-TITLE_OK",
                "document_identifier": "111",
                "document_type": "Final Written Decision: original",
                "document_title": (
                    "Final Written Decision Determining All Challenged Claims "
                    "Unpatentable 35 U.S.C. § 318(a)"
                ),
                "file_download_uri": "https://example/111.pdf",
                "decision_issue_date": "2023-05-23",
            },
            # 2. unresolvable + uncached → KEEP
            {
                "trial_number": "IPR2022-NEEDS_PDF",
                "document_identifier": "222",
                "document_type": "Final Written Decision: original",
                "document_title": "Final Written Decision",  # no granular outcome
                "file_download_uri": "https://example/222.pdf",
                "decision_issue_date": "2023-06-01",
            },
            # 3. status-resolvable → excluded by status layer (Institution Denied)
            {
                "trial_number": "IPR2022-STATUS_OK",
                "document_identifier": "333",
                "document_type": "Final Written Decision: original",
                "document_title": "Final Written Decision",
                "file_download_uri": "https://example/333.pdf",
                "decision_issue_date": "2023-06-02",
            },
            # 4. unresolvable but PDF already cached → excluded by cache
            {
                "trial_number": "IPR2022-CACHED",
                "document_identifier": "444",
                "document_type": "Final Written Decision: original",
                "document_title": "Final Written Decision",
                "file_download_uri": "https://example/444.pdf",
                "decision_issue_date": "2023-06-03",
            },
            # 5. on-rehearing variant → excluded (no "original" marker)
            {
                "trial_number": "IPR2022-REHEARING",
                "document_identifier": "555",
                "document_type": "Final Written Decision on Rehearing",
                "document_title": "Final Written Decision on Rehearing",
                "file_download_uri": "https://example/555.pdf",
                "decision_issue_date": "2023-06-04",
            },
        ]
    )


def _trials_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trial_number": "IPR2022-TITLE_OK", "trial_type": "IPR",
             "trial_status": "Final Written Decision"},
            {"trial_number": "IPR2022-NEEDS_PDF", "trial_type": "IPR",
             "trial_status": "Final Written Decision"},
            {"trial_number": "IPR2022-STATUS_OK", "trial_type": "IPR",
             "trial_status": "Institution Denied"},
            {"trial_number": "IPR2022-CACHED", "trial_type": "IPR",
             "trial_status": "Final Written Decision"},
            {"trial_number": "IPR2022-REHEARING", "trial_type": "IPR",
             "trial_status": "Final Written Decision"},
        ]
    )


@pytest.fixture
def seeded_storage(tmp_path: Path) -> LocalStorage:
    """LocalStorage seeded with one cached FWD text blob."""
    storage = LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")
    storage.save_blob(Stage.DECISION_TEXTS, "444", "txt", b"cached opinion text")
    return storage


def test_enumerate_excludes_status_title_cached_and_rehearing(seeded_storage):
    """Only unresolvable, uncached, original FWDs survive the gap filter."""
    out = enumerate_missing_fwd_pdfs(
        seeded_storage,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
    )

    assert list(out["trial_number"]) == ["IPR2022-NEEDS_PDF"]
    assert list(out["document_identifier"]) == ["222"]
    assert list(out["file_download_uri"]) == ["https://example/222.pdf"]


def test_enumerate_excludes_non_ipr(seeded_storage):
    """Non-IPR trial types are filtered out of the FWD-PDF backlog."""
    trials = _trials_frame()
    trials.loc[trials["trial_number"] == "IPR2022-NEEDS_PDF", "trial_type"] = "PGR"
    out = enumerate_missing_fwd_pdfs(
        seeded_storage,
        trials=trials,
        decisions=_decisions_frame(),
    )
    assert out.empty


def test_enumerate_handles_empty_decisions_frame(tmp_path: Path):
    """An empty decisions frame yields a typed empty frame, not an error."""
    storage = LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")
    out = enumerate_missing_fwd_pdfs(
        storage,
        trials=_trials_frame(),
        decisions=pd.DataFrame(),
    )
    assert out.empty
    assert list(out.columns) == [
        "trial_number",
        "document_identifier",
        "file_download_uri",
        "document_title",
        "decision_issue_date",
    ]
