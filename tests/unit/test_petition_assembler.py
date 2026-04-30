"""Lock in petition_assembler behavior across all per-trial outcomes:
picker hit, picker miss → quarantine, no documents → quarantine, T₀ mismatch
between proceedings and documents endpoints (warning, proceedings wins).
"""

import logging
from datetime import date

import pandas as pd

from ml_uspto.parse.petitions import assemble_petitions
from ml_uspto.schemas.enums import QuarantineReason


def _doc_row(
    trial, number, title, *, doc_id=None, category="PETITION", uri=None, filing_date="2024-01-15"
):
    return {
        "trialNumber": trial,
        # trialMetaData is intentionally populated to verify the assembler
        # never reads it (lagged-stamp leakage hazard).
        "trialMetaData": {"trialStatusCategory": "Pending"},
        "documentData": {
            "documentIdentifier": doc_id or f"{trial}-doc-{number}",
            "documentNumber": number,
            "documentTitleText": title,
            "documentCategory": category,
            "documentFilingDate": filing_date,
            "fileDownloadURI": uri
            or f"https://api.uspto.gov/api/v1/patent/ptab-files/IPR/{trial}-{number}.pdf",
        },
    }


def _trials_df(rows):
    return pd.DataFrame(rows, columns=["trial_number", "petition_filing_date"])


def test_happy_path_one_trial_one_petition():
    raw = [
        _doc_row("IPR2024-00001", 1, "Power of Attorney", category="Paper"),
        _doc_row("IPR2024-00001", 2, "Petition for Inter Partes Review", category="PETITION"),
    ]
    trials = _trials_df([("IPR2024-00001", "2024-01-15")])
    petitions, quarantine = assemble_petitions(raw, trials)

    assert len(petitions) == 1
    assert len(quarantine) == 0
    p = petitions[0]
    assert p.trial_number == "IPR2024-00001"
    assert p.petition_number == "2"
    assert p.petition_category == "PETITION"
    assert p.petition_filing_date_doc == date(2024, 1, 15)
    assert p.petition_pdf_uri.endswith("IPR2024-00001-2.pdf")


def test_corrected_petition_loses_to_original_via_picker():
    """Both rows are in the PETITION bucket — picker takes the lowest paper #."""
    raw = [
        _doc_row("IPR2024-00002", 8, "Corrected Petition", category="PETITION"),
        _doc_row("IPR2024-00002", 2, "Petition for Inter Partes Review", category="PETITION"),
    ]
    trials = _trials_df([("IPR2024-00002", "2024-01-15")])
    petitions, _ = assemble_petitions(raw, trials)
    assert petitions[0].petition_number == "2"


def test_picker_miss_emits_quarantine_with_sample_titles():
    raw = [
        _doc_row("IPR2024-00003", 1, "Power of Attorney", category="Paper"),
        _doc_row(
            "IPR2024-00003", 5, "Notice of Filing Date Accorded to Petition", category="Paper"
        ),
    ]
    trials = _trials_df([("IPR2024-00003", "2024-02-01")])
    petitions, quarantine = assemble_petitions(raw, trials)

    assert petitions == []
    assert len(quarantine) == 1
    q = quarantine[0]
    assert q.trial_number == "IPR2024-00003"
    assert q.reason is QuarantineReason.PICKER_NO_MATCH
    assert q.n_candidates == 2
    assert "Power of Attorney" in q.sample_titles


def test_trial_with_no_documents_emits_no_documents_quarantine():
    raw: list[dict] = []
    trials = _trials_df([("IPR2024-00004", "2024-03-01")])
    petitions, quarantine = assemble_petitions(raw, trials)

    assert petitions == []
    assert len(quarantine) == 1
    assert quarantine[0].reason is QuarantineReason.NO_DOCUMENTS
    assert quarantine[0].n_candidates == 0


def test_t0_mismatch_logs_warning_but_proceedings_wins(caplog):
    raw = [
        _doc_row(
            "IPR2024-00005",
            2,
            "Petition for Inter Partes Review",
            category="PETITION",
            filing_date="2024-01-20",
        ),
    ]
    trials = _trials_df([("IPR2024-00005", "2024-01-15")])
    with caplog.at_level(logging.WARNING, logger="ml_uspto.parse.petitions"):
        petitions, _ = assemble_petitions(raw, trials)

    assert len(petitions) == 1
    assert petitions[0].petition_filing_date_doc == date(2024, 1, 20)
    assert any("T₀ mismatch" in m for m in caplog.messages)


def test_t0_match_does_not_warn(caplog):
    raw = [
        _doc_row("IPR2024-00006", 2, "Petition for Inter Partes Review", filing_date="2024-04-10"),
    ]
    trials = _trials_df([("IPR2024-00006", "2024-04-10")])
    with caplog.at_level(logging.WARNING, logger="ml_uspto.parse.petitions"):
        assemble_petitions(raw, trials)
    assert not any("T₀ mismatch" in m for m in caplog.messages)


def test_assembler_never_reads_trial_metadata():
    """Even if the documents row's trialMetaData carries a different
    petitionFilingDate, the assembler must build the Petition from
    documentData only — proceedings is the canonical T₀ source.
    """
    raw = [
        {
            "trialNumber": "IPR2024-00007",
            "trialMetaData": {"petitionFilingDate": "1999-01-01"},
            "documentData": {
                "documentIdentifier": "abc",
                "documentNumber": 2,
                "documentTitleText": "Petition for Inter Partes Review",
                "documentCategory": "PETITION",
                "documentFilingDate": "2024-05-12",
                "fileDownloadURI": "https://x/y.pdf",
            },
        }
    ]
    trials = _trials_df([("IPR2024-00007", "2024-05-12")])
    petitions, _ = assemble_petitions(raw, trials)
    assert petitions[0].petition_filing_date_doc == date(2024, 5, 12)


def test_mixed_outcomes_one_call():
    raw = [
        _doc_row("IPR-A", 2, "Petition for Inter Partes Review"),
        _doc_row("IPR-B", 1, "Power of Attorney", category="Paper"),
    ]
    trials = _trials_df(
        [
            ("IPR-A", "2024-01-15"),  # hit
            ("IPR-B", "2024-02-15"),  # picker miss
            ("IPR-C", "2024-03-15"),  # absent from raw
        ]
    )
    petitions, quarantine = assemble_petitions(raw, trials)

    assert {p.trial_number for p in petitions} == {"IPR-A"}
    reasons = {q.trial_number: q.reason for q in quarantine}
    assert reasons == {
        "IPR-B": QuarantineReason.PICKER_NO_MATCH,
        "IPR-C": QuarantineReason.NO_DOCUMENTS,
    }


def test_t0_cross_check_handles_pandas_timestamp_column():
    raw = [
        _doc_row("IPR2024-00008", 2, "Petition for Inter Partes Review", filing_date="2024-06-01"),
    ]
    trials = pd.DataFrame(
        {
            "trial_number": ["IPR2024-00008"],
            "petition_filing_date": pd.to_datetime(["2024-06-01"]),
        }
    )
    petitions, _ = assemble_petitions(raw, trials)
    assert petitions[0].petition_filing_date_doc == date(2024, 6, 1)


def test_trials_without_petition_filing_date_column_skips_t0_check(caplog):
    raw = [_doc_row("IPR2024-00009", 2, "Petition for Inter Partes Review")]
    trials = pd.DataFrame({"trial_number": ["IPR2024-00009"]})
    with caplog.at_level(logging.WARNING, logger="ml_uspto.parse.petitions"):
        petitions, _ = assemble_petitions(raw, trials)
    assert len(petitions) == 1
    assert not any("T₀" in m for m in caplog.messages)
