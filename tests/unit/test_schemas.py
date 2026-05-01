"""Round-trip tests for the core data contracts.

These exist to fail loudly the day the upstream API schema shifts: when a
field disappears or changes type, validation breaks here before the change
silently corrupts a downstream feature pipeline.
"""

from datetime import date, datetime

from ml_uspto.schemas.models import (
    PatentFetchResult,
    PdfFetchManifestRow,
    Petition,
    PetitionTextDoc,
    PetitionTextFeatures,
    Proceeding,
)

SAMPLE_PROCEEDING = {
    "trialNumber": "IPR2026-00339",
    "trialMetaData": {
        "trialTypeCode": "IPR",
        "trialStatusCategory": "Trial Instituted",
        "petitionFilingDate": "2026-01-15",
        "accordedFilingDate": "2026-01-20",
    },
    "patentOwnerData": {
        "patentNumber": "10000000",
        "realPartyInInterestName": "Acme Corp",
        "technologyCenterNumber": "2600",
        "groupArtUnitNumber": "2645",
    },
    "regularPetitionerData": {
        "realPartyInInterestName": "Beta LLC",
    },
}


def test_proceeding_round_trip():
    """Validate and serialize a representative proceeding payload."""
    p = Proceeding.model_validate(SAMPLE_PROCEEDING)

    assert p.trial_number == "IPR2026-00339"
    assert p.trial_meta_data.trial_type == "IPR"
    assert p.trial_meta_data.petition_filing_date == date(2026, 1, 15)
    assert p.patent_owner_data.patent_number == "10000000"
    assert p.patent_owner_data.technology_center == "2600"
    assert p.regular_petitioner_data.real_party == "Beta LLC"

    dumped = p.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert dumped["trialNumber"] == "IPR2026-00339"
    assert dumped["trialMetaData"]["trialTypeCode"] == "IPR"
    assert dumped["trialMetaData"]["petitionFilingDate"] == "2026-01-15"


def test_proceeding_tolerates_missing_optional_fields():
    """Allow absent optional proceeding subfields."""
    minimal = {"trialNumber": "IPR2026-00001"}
    p = Proceeding.model_validate(minimal)

    assert p.trial_number == "IPR2026-00001"
    assert p.trial_meta_data.trial_type is None
    assert p.patent_owner_data.patent_number is None


def test_proceeding_ignores_unknown_fields():
    """Ignore unexpected upstream fields during proceeding validation."""
    payload = {**SAMPLE_PROCEEDING, "unexpectedTopLevel": "ignore-me"}
    p = Proceeding.model_validate(payload)

    assert p.trial_number == "IPR2026-00339"


# ---------------------------------------------------------------------------
# Smoke tests for the new pipeline-seam models. They are constructed
# internally (not parsed from API JSON), so we only check construction +
# defaults + nesting rather than alias round-trips.
# ---------------------------------------------------------------------------


def test_petition_minimal_construction():
    """Construct a minimal internal Petition model."""
    p = Petition(
        trial_number="IPR2024-00123",
        petition_document_id="doc-abc",
        petition_filing_date_doc=date(2024, 3, 1),
        petition_pdf_uri="https://example.test/petition.pdf",
    )
    assert p.petition_title is None
    assert p.petition_category is None


def test_pdf_fetch_manifest_row_records_failure():
    """Represent failed PDF fetches without bytes or hashes."""
    row = PdfFetchManifestRow(
        trial_number="IPR2024-00123",
        http_status=503,
        fetched_at=datetime(2026, 4, 28, 12, 0, 0),
        error="Service Unavailable",
    )
    assert row.bytes is None
    assert row.sha256 is None
    assert row.http_status == 503


def test_petition_text_doc_holds_pages():
    """Store extracted petition pages in the text-doc model."""
    doc = PetitionTextDoc(
        trial_number="IPR2024-00123",
        page_count=2,
        char_count=10,
        pages=["hello", "world"],
        pdfplumber_version="0.11.0",
        extracted_at=datetime(2026, 4, 28, 12, 0, 0),
    )
    assert len(doc.pages) == 2


def test_petition_text_features_word_count_optional_others_zero():
    """Default absent petition-text feature counts to zero."""
    feats = PetitionTextFeatures(
        trial_number="IPR2024-00123",
        petition_page_count=88,
        text_doc_sha256="0" * 64,
        extracted_at=datetime(2026, 4, 28, 12, 0, 0),
    )
    assert feats.petition_word_count is None
    assert feats.n_claims_challenged == 0
    assert feats.has_sotera_stipulation is False
    assert feats.n_grounds_102 == 0


def test_patent_fetch_result_shape():
    """Represent successful and missing patent fetch outcomes."""
    success = PatentFetchResult(
        application_number="14709428",
        raw_record={"applicationNumberText": "14709428"},
    )
    assert success.raw_record == {"applicationNumberText": "14709428"}

    miss = PatentFetchResult(application_number="00000000")
    assert miss.raw_record is None
