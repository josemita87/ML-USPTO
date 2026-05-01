"""Lock in petition_assembler behavior — picker hit / picker miss outcomes
on a single corpus pass. The assembler is pure on documents/search records;
it returns only `Petition` rows (picker misses are silently dropped) and
the joiner reconciles labeled trials against the petitions roster.
"""

from datetime import date

from ml_uspto.parse.petitions import assemble_petitions


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


def test_happy_path_one_trial_one_petition():
    raw = [
        _doc_row("IPR2024-00001", 1, "Power of Attorney", category="Paper"),
        _doc_row("IPR2024-00001", 2, "Petition for Inter Partes Review", category="PETITION"),
    ]
    petitions = assemble_petitions(raw)

    assert len(petitions) == 1
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
    petitions = assemble_petitions(raw)
    assert petitions[0].petition_number == "2"


def test_picker_miss_drops_the_trial():
    """When every candidate fails the filter, the trial is omitted from
    the petitions output. No quarantine ledger is emitted.
    """
    raw = [
        _doc_row("IPR2024-00003", 1, "Power of Attorney", category="Paper"),
        _doc_row(
            "IPR2024-00003", 5, "Notice of Filing Date Accorded to Petition", category="Paper"
        ),
    ]
    petitions = assemble_petitions(raw)
    assert petitions == []


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
    petitions = assemble_petitions(raw)
    assert petitions[0].petition_filing_date_doc == date(2024, 5, 12)


def test_mixed_outcomes_one_call():
    raw = [
        _doc_row("IPR-A", 2, "Petition for Inter Partes Review"),
        _doc_row("IPR-B", 1, "Power of Attorney", category="Paper"),
    ]
    petitions = assemble_petitions(raw)
    assert {p.trial_number for p in petitions} == {"IPR-A"}


def test_empty_input_returns_empty_list():
    assert assemble_petitions([]) == []
