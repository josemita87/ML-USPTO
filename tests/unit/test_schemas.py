"""Round-trip tests for the core data contracts.

These exist to fail loudly the day the upstream API schema shifts: when a
field disappears or changes type, validation breaks here before the change
silently corrupts a downstream feature pipeline.
"""

from datetime import date

from ml_uspto.schemas.models import Proceeding

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
