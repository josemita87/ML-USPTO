"""Lock in the two-step patent pipeline:
parse → PatentFileWrapper → cleanse_at_t0 → PatentSnapshot → extract_features → PatentFeatures.

The first test pins both the snapshot's leakage-cleansing behavior
(post-T₀ events, TRIAL events, post-T₀ assignments all dropped) and the
downstream feature counts on the same fixture, so we can detect either
boundary breaking independently.
"""

from datetime import date

import pytest

from ml_uspto.features.patents import cleanse_at_t0, extract_features
from ml_uspto.parse.patents import parse_patent_wrapper


def _full_fixture() -> dict:
    return {
        "applicationNumberText": "14709428",
        "applicationMetaData": {
            "cpcClassificationBag": ["H04W 88/06", "G06F 17/00"],
        },
        "eventDataBag": [
            {"eventCode": "IEXX", "eventDate": "2015-05-11"},
            {"eventCode": "CTNF", "eventDate": "2016-01-01"},
            {"eventCode": "WIDS", "eventDate": "2016-02-01"},
            {"eventCode": "M2551", "eventDate": "2020-01-01"},
            {"eventCode": "ZZZZ", "eventDate": "2021-01-01"},
            {"eventCode": "TRIALPET", "eventDate": "2022-05-23"},
            {"eventCode": "TRIALFWD", "eventDate": "2024-01-01"},
            {"eventCode": "CTFR", "eventDate": "2023-01-01"},
        ],
        "assignmentBag": [
            {
                "assignmentReceivedDate": "2018-01-01",
                "assigneeBag": [
                    {"assigneeNameText": "Apple Inc."},
                    {"assigneeNameText": "APPLE INC"},
                ],
            },
            {
                "assignmentReceivedDate": "2022-06-01",
                "assigneeBag": [{"assigneeNameText": "Post T0 LLC"}],
            },
        ],
        "parentContinuityBag": [
            {"parentApplicationNumberText": "11111111"},
            {"parentApplicationNumberText": "22222222"},
        ],
    }


def test_cleanse_drops_post_t0_and_trial_events_and_post_t0_assignments():
    snapshot = cleanse_at_t0(
        parse_patent_wrapper(_full_fixture()),
        trial_number="IPR2022-01002",
        petition_filing_date=date(2022, 5, 23),
    )
    assert snapshot.application_number == "14709428"
    # 5 surviving events: IEXX, CTNF, WIDS, M2551, ZZZZ.
    # Dropped: TRIALPET (banned prefix), TRIALFWD (banned prefix),
    # CTFR (post-T₀).
    assert {(e.event_code, str(e.event_date)) for e in snapshot.events} == {
        ("IEXX", "2015-05-11"),
        ("CTNF", "2016-01-01"),
        ("WIDS", "2016-02-01"),
        ("M2551", "2020-01-01"),
        ("ZZZZ", "2021-01-01"),
    }
    # Only the 2018 assignment survives; the 2022-06-01 one is post-T₀.
    assert len(snapshot.assignments) == 1
    assert snapshot.assignments[0].assignment_received_date == date(2018, 1, 1)
    assert snapshot.parent_continuity[0].application_number == "11111111"
    assert snapshot.cpc_classifications == ["H04W 88/06", "G06F 17/00"]


def test_extract_features_pins_count_and_span_outputs():
    snapshot = cleanse_at_t0(
        parse_patent_wrapper(_full_fixture()),
        trial_number="IPR2022-01002",
        petition_filing_date=date(2022, 5, 23),
    )
    features = extract_features(snapshot)

    assert features.cpc_section == "H"
    assert features.n_events_pre_t0 == 5
    assert features.n_pe_pre_t0 == 1
    assert features.n_ex_pre_t0 == 1
    assert features.n_aa_pre_t0 == 1
    assert features.n_maint_pre_t0 == 1
    assert features.n_other_pre_t0 == 1
    assert features.n_office_actions == 1
    assert features.prosecution_span_days == 2062
    assert features.n_assignments_pre_t0 == 1
    assert features.n_distinct_assignees_pre_t0 == 1
    assert features.days_since_last_assignment == 1603
    assert features.n_parent_applications == 2
    assert features.days_grant_to_petition is None


def test_pipeline_accepts_file_wrapper_bag_envelope():
    payload = {
        "patentFileWrapperDataBag": [
            {
                "applicationNumberText": "17000000",
                "applicationMetaData": {"cpcClassificationBag": ["G06F 17/00"]},
                "eventDataBag": [{"eventCode": "CTNF", "eventDate": "2020-01-01"}],
            }
        ]
    }
    snapshot = cleanse_at_t0(
        parse_patent_wrapper(payload),
        trial_number="IPR2024-00001",
        petition_filing_date="2024-01-01",
    )
    features = extract_features(snapshot)

    assert features.cpc_section == "G"
    assert features.n_ex_pre_t0 == 1


def test_cleanse_rejects_unparseable_t0():
    with pytest.raises(ValueError, match="petition_filing_date"):
        cleanse_at_t0(
            parse_patent_wrapper({"applicationNumberText": "17000000"}),
            trial_number="IPR2024-00001",
            petition_filing_date="not-a-date",
        )


def test_cleanse_rejects_wrapper_missing_application_number():
    with pytest.raises(ValueError, match="application_number"):
        cleanse_at_t0(
            parse_patent_wrapper({}),
            trial_number="IPR2024-00001",
            petition_filing_date="2024-01-01",
        )
