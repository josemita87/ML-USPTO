from datetime import date

import pytest

from ml_uspto.parse.patent_aggregator import aggregate_patent


def test_aggregate_patent_filters_post_t0_and_trial_events():
    payload = {
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

    features = aggregate_patent(
        payload,
        trial_number="IPR2022-01002",
        application_number="14709428",
        petition_filing_date=date(2022, 5, 23),
    )

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


def test_aggregate_patent_accepts_file_wrapper_bag_envelope():
    payload = {
        "patentFileWrapperDataBag": [
            {
                "applicationMetaData": {"cpcClassificationBag": ["G06F 17/00"]},
                "eventDataBag": [{"eventCode": "CTNF", "eventDate": "2020-01-01"}],
            }
        ]
    }

    features = aggregate_patent(
        payload,
        trial_number="IPR2024-00001",
        application_number="17000000",
        petition_filing_date="2024-01-01",
    )

    assert features.cpc_section == "G"
    assert features.n_ex_pre_t0 == 1


def test_aggregate_patent_rejects_missing_t0():
    with pytest.raises(ValueError, match="petition_filing_date"):
        aggregate_patent(
            {},
            trial_number="IPR2024-00001",
            application_number="17000000",
            petition_filing_date="not-a-date",
        )
