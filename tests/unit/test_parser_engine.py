"""Smoke test for the declarative parser engine."""

from ml_uspto.parse.engine import flatten, flatten_records, load_parser_config


def test_proceedings_parser_flattens_known_payload():
    records = [
        {
            "trialNumber": "IPR2026-00339",
            "trialMetaData": {"trialTypeCode": "IPR", "petitionFilingDate": "2026-01-15"},
            "patentOwnerData": {"patentNumber": "10000000", "technologyCenterNumber": "2600"},
            "regularPetitionerData": {"realPartyInInterestName": "Beta LLC"},
        }
    ]
    df = flatten(records, "proceedings")

    assert list(df.columns) == list(load_parser_config("proceedings")["columns"])
    row = df.iloc[0]
    assert row["trial_number"] == "IPR2026-00339"
    assert row["trial_type"] == "IPR"
    assert row["petition_filing_date"] == "2026-01-15"
    assert row["patent_number"] == "10000000"
    assert row["technology_center"] == "2600"
    assert row["petitioner_real_party"] == "Beta LLC"


def test_missing_paths_become_none():
    config = {"columns": {"a": "x.y", "b": "z"}}
    df = flatten_records([{"x": {}}], config)
    assert df.iloc[0]["a"] is None
    assert df.iloc[0]["b"] is None
