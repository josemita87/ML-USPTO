"""Smoke test for the declarative flatten engine."""

from ml_uspto.parse.flatten import flatten, flatten_records, load_parser_config
from ml_uspto.parse.schemas.enums import Parser


def test_proceedings_parser_flattens_known_payload():
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
    cfg = load_parser_config(Parser.DECISIONS)
    assert "trial_number" in cfg["columns"]
    assert cfg["columns"]["decision_issue_date"] == "decisionData.decisionIssueDate"


def test_patents_parser_flattens_static_payload():
    records = [
        {
            "applicationNumberText": "14709428",
            "applicationMetaData": {
                "filingDate": "2015-05-11",
                "effectiveFilingDate": "2000-07-17",
                "applicationTypeCode": "UTL",
                "firstInventorToFileIndicator": "Y",
                "nationalStageIndicator": "N",
                "inventorBag": [
                    {"correspondenceAddressBag": [{"countryCode": "US"}]},
                    {"correspondenceAddressBag": [{"countryCode": "US"}, {"countryCode": "CA"}]},
                ],
                "cpcClassificationBag": ["H04W 88/06", "G06F 17/00"],
            },
            "patentTermAdjustmentData": {"aDelayQuantity": 1, "adjustmentTotalQuantity": 5},
            "recordAttorney": {"attorneyBag": [{"name": "A"}, {"name": "B"}]},
        }
    ]
    df = flatten(records, Parser.PATENTS)

    row = df.iloc[0]
    assert row["application_number"] == "14709428"
    assert bool(row["first_inventor_to_file"]) is True
    assert bool(row["national_stage"]) is False
    assert row["n_inventors"] == 2
    assert row["inventor_country_codes"] == ["US", "CA"]
    assert row["cpc_codes"] == ["H04W 88/06", "G06F 17/00"]
    assert row["n_attorneys_of_record"] == 2
    assert row["pta_a_delay"] == 1
    assert row["pta_total"] == 5


def test_missing_paths_become_none():
    config = {"columns": {"a": "x.y", "b": "z"}}
    df = flatten_records([{"x": {}}], config)
    assert df.iloc[0]["a"] is None
    assert df.iloc[0]["b"] is None
