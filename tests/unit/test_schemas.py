"""Round-trip tests for the core data contracts.

These exist to fail loudly the day the upstream API schema shifts: when a
field disappears or changes type, validation breaks here before the change
silently corrupts a downstream feature pipeline.
"""

from datetime import date, datetime

from ml_uspto.schemas.enums import PatentQuarantineReason, QuarantineReason
from ml_uspto.schemas.models import (
    JoinedTrial,
    PatentFeatures,
    PatentFetchResult,
    PatentQuarantineEntry,
    PdfFetchManifestRow,
    Petition,
    PetitionTextDoc,
    PetitionTextFeatures,
    Proceeding,
    QuarantineEntry,
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
    minimal = {"trialNumber": "IPR2026-00001"}
    p = Proceeding.model_validate(minimal)

    assert p.trial_number == "IPR2026-00001"
    assert p.trial_meta_data.trial_type is None
    assert p.patent_owner_data.patent_number is None


def test_proceeding_ignores_unknown_fields():
    payload = {**SAMPLE_PROCEEDING, "unexpectedTopLevel": "ignore-me"}
    p = Proceeding.model_validate(payload)

    assert p.trial_number == "IPR2026-00339"


# ---------------------------------------------------------------------------
# Smoke tests for the new pipeline-seam models. They are constructed
# internally (not parsed from API JSON), so we only check construction +
# defaults + nesting rather than alias round-trips.
# ---------------------------------------------------------------------------


def test_petition_minimal_construction():
    p = Petition(
        trial_number="IPR2024-00123",
        petition_document_id="doc-abc",
        petition_filing_date_doc=date(2024, 3, 1),
        petition_pdf_uri="https://example.test/petition.pdf",
    )
    assert p.petition_title is None
    assert p.petition_category is None


def test_quarantine_entry_default_sample_titles_is_empty():
    q = QuarantineEntry(
        trial_number="IPR2014-00999",
        reason=QuarantineReason.PICKER_NO_MATCH,
        n_candidates=0,
    )
    assert q.sample_titles == []


def test_patent_features_counters_default_to_zero():
    f = PatentFeatures(trial_number="IPR2024-00123", application_number="14709428")
    assert f.n_events_pre_t0 == 0
    assert f.n_ex_pre_t0 == 0
    assert f.n_other_pre_t0 == 0
    assert f.n_office_actions == 0
    assert f.days_grant_to_petition is None


def test_joined_trial_nests_patent_features():
    pf = PatentFeatures(
        trial_number="IPR2024-00123",
        application_number="14709428",
        cpc_section="G",
        n_events_pre_t0=42,
    )
    jt = JoinedTrial(
        trial_number="IPR2024-00123",
        petition_filing_date=date(2024, 3, 1),
        cancelled=1,
        petition_pdf_uri="https://example.test/petition.pdf",
        petition_filing_date_doc=date(2024, 3, 1),
        patent_features=pf,
    )
    assert jt.patent_features is not None
    assert jt.patent_features.cpc_section == "G"
    assert jt.cancelled == 1


def test_joined_trial_allows_missing_patent_features():
    jt = JoinedTrial(
        trial_number="IPR2024-00123",
        petition_filing_date=date(2024, 3, 1),
        cancelled=0,
        petition_pdf_uri="https://example.test/petition.pdf",
        petition_filing_date_doc=date(2024, 3, 1),
    )
    assert jt.patent_features is None


def test_pdf_fetch_manifest_row_records_failure():
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


def test_patent_quarantine_and_fetch_result_shape():
    q = PatentQuarantineEntry(
        application_number="14709428",
        reason=PatentQuarantineReason.NOT_FOUND,
        http_status=404,
        error="missing",
    )
    result = PatentFetchResult(application_number="14709428", quarantine=q)

    assert result.raw_record is None
    assert result.quarantine is not None
    assert result.quarantine.reason is PatentQuarantineReason.NOT_FOUND
