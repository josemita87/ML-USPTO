"""Smoke test for the declarative flatten engine."""

from ml_uspto.parse.flatten import flatten, flatten_records, load_parser_config
from ml_uspto.parse.schemas.enums import Parser


def test_proceedings_parser_flattens_known_payload():
    """Proceedings parser maps known nested keys onto the configured column names."""
    records = [
        {
            "trialNumber": "IPR2026-00339",
            "trialMetaData": {"trialTypeCode": "IPR", "petitionFilingDate": "2026-01-15"},
            "patentOwnerData": {"patentNumber": "10000000", "technologyCenterNumber": "2600"},
            "regularPetitionerData": {"realPartyInInterestName": "Beta LLC"},
        }
    ]
    df = flatten(records, Parser.PROCEEDINGS)

    assert list(df.columns) == list(load_parser_config(Parser.PROCEEDINGS)["columns"])
    row = df.iloc[0]
    assert row["trial_number"] == "IPR2026-00339"
    assert row["trial_type"] == "IPR"
    assert row["petition_filing_date"] == "2026-01-15"
    assert row["patent_number"] == "10000000"
    assert row["technology_center"] == "2600"
    assert row["petitioner_real_party"] == "Beta LLC"


def test_decisions_parser_loads():
    """Decisions parser config exposes the trial_number and decision_issue_date columns."""
    cfg = load_parser_config(Parser.DECISIONS)
    assert "trial_number" in cfg["columns"]
    assert cfg["columns"]["decision_issue_date"] == "decisionData.decisionIssueDate"


def test_missing_paths_become_none():
    """Unresolved dotted paths yield None instead of raising."""
    config = {"columns": {"a": "x.y", "b": "z"}}
    df = flatten_records([{"x": {}}], config)
    assert df.iloc[0]["a"] is None
    assert df.iloc[0]["b"] is None
