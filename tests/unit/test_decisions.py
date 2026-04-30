from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.decisions import enumerate_missing_fwd_pdfs


def _decisions_page() -> dict:
    """Five FWD-shaped decision rows covering each filter layer."""
    rows = [
        # 1. title-resolvable → excluded by title regex
        {
            "trialNumber": "IPR2022-TITLE_OK",
            "documentData": {
                "documentIdentifier": "111",
                "documentTypeDescriptionText": "Final Written Decision: original",
                "documentTitleText": (
                    "Final Written Decision Determining All Challenged Claims "
                    "Unpatentable 35 U.S.C. § 318(a)"
                ),
                "fileDownloadURI": "https://example/111.pdf",
            },
            "decisionData": {"decisionIssueDate": "2023-05-23"},
        },
        # 2. unresolvable + uncached → KEEP
        {
            "trialNumber": "IPR2022-NEEDS_PDF",
            "documentData": {
                "documentIdentifier": "222",
                "documentTypeDescriptionText": "Final Written Decision: original",
                "documentTitleText": "Final Written Decision",  # no granular outcome
                "fileDownloadURI": "https://example/222.pdf",
            },
            "decisionData": {"decisionIssueDate": "2023-06-01"},
        },
        # 3. status-resolvable → excluded by status layer (Institution Denied)
        {
            "trialNumber": "IPR2022-STATUS_OK",
            "documentData": {
                "documentIdentifier": "333",
                "documentTypeDescriptionText": "Final Written Decision: original",
                "documentTitleText": "Final Written Decision",
                "fileDownloadURI": "https://example/333.pdf",
            },
            "decisionData": {"decisionIssueDate": "2023-06-02"},
        },
        # 4. unresolvable but PDF already cached → excluded by cache
        {
            "trialNumber": "IPR2022-CACHED",
            "documentData": {
                "documentIdentifier": "444",
                "documentTypeDescriptionText": "Final Written Decision: original",
                "documentTitleText": "Final Written Decision",
                "fileDownloadURI": "https://example/444.pdf",
            },
            "decisionData": {"decisionIssueDate": "2023-06-03"},
        },
        # 5. on-rehearing variant → excluded (no "original" marker)
        {
            "trialNumber": "IPR2022-REHEARING",
            "documentData": {
                "documentIdentifier": "555",
                "documentTypeDescriptionText": "Final Written Decision on Rehearing",
                "documentTitleText": "Final Written Decision on Rehearing",
                "fileDownloadURI": "https://example/555.pdf",
            },
            "decisionData": {"decisionIssueDate": "2023-06-04"},
        },
    ]
    return {"patentTrialDocumentDataBag": rows}


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
    storage = LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")
    storage.save_object(Stage.DECISIONS.value, "page_001", _decisions_page())
    storage.save_blob(Stage.DECISION_PDFS.value, "444", "pdf", b"%PDF-1.4 cached")
    return storage


def test_enumerate_excludes_status_title_cached_and_rehearing(seeded_storage):
    out = enumerate_missing_fwd_pdfs(seeded_storage, trials=_trials_frame())

    assert list(out["trial_number"]) == ["IPR2022-NEEDS_PDF"]
    assert list(out["document_identifier"]) == ["222"]
    assert list(out["file_download_uri"]) == ["https://example/222.pdf"]


def test_enumerate_excludes_non_ipr(seeded_storage):
    trials = _trials_frame()
    trials.loc[trials["trial_number"] == "IPR2022-NEEDS_PDF", "trial_type"] = "PGR"
    out = enumerate_missing_fwd_pdfs(seeded_storage, trials=trials)
    assert out.empty


def test_enumerate_skips_recent_failures(seeded_storage):
    failures = pd.DataFrame(
        [{
            "document_identifier": "222",
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "http_status": 503,
        }]
    )
    out = enumerate_missing_fwd_pdfs(
        seeded_storage, trials=_trials_frame(), failures=failures, retry_after_days=7
    )
    assert out.empty


def test_enumerate_retries_old_failures(seeded_storage):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    failures = pd.DataFrame(
        [{"document_identifier": "222", "failed_at": old, "http_status": 503}]
    )
    out = enumerate_missing_fwd_pdfs(
        seeded_storage, trials=_trials_frame(), failures=failures, retry_after_days=7
    )
    assert list(out["trial_number"]) == ["IPR2022-NEEDS_PDF"]


def test_enumerate_handles_empty_decisions_cache(tmp_path: Path):
    storage = LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")
    out = enumerate_missing_fwd_pdfs(storage, trials=_trials_frame())
    assert out.empty
    assert list(out.columns) == [
        "trial_number",
        "document_identifier",
        "file_download_uri",
        "document_title",
        "decision_issue_date",
    ]
