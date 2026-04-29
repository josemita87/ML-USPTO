from datetime import date

import pandas as pd
import pytest

from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.joiner import join_all


def _trials_frame() -> pd.DataFrame:
    """Three trials covering each label-resolution path:
      - LABEL_BY_STATUS_0 → trial_status = "Institution Denied"
      - LABEL_BY_FWD_1    → trial_status = "Final Written Decision" + decisions FWD
                            with trialOutcome = "All Challenged Claims Unpatentable"
      - QUARANTINED       → no petition row → dropped by inner-join
    """
    return pd.DataFrame(
        [
            {
                "trial_number": "IPR2022-LABEL_BY_STATUS_0",
                "trial_type": "IPR",
                "trial_status": "Institution Denied",
                "petition_filing_date": date(2022, 1, 15),
                "patent_number": "US10000000",
                "application_number": "14000001",
                "grant_date": date(2018, 6, 1),
                "technology_center": "2100",
            },
            {
                "trial_number": "IPR2022-LABEL_BY_FWD_1",
                "trial_type": "IPR",
                "trial_status": "Final Written Decision",
                "petition_filing_date": date(2022, 5, 23),
                "patent_number": "US10000001",
                "application_number": "14000002",
                "grant_date": date(2017, 1, 1),
                "technology_center": "2400",
            },
            {
                "trial_number": "IPR2022-QUARANTINED",
                "trial_type": "IPR",
                "trial_status": "Terminated-Settled",
                "petition_filing_date": date(2022, 8, 1),
                "patent_number": "US10000002",
                "application_number": "14000003",
                "grant_date": date(2019, 3, 1),
                "technology_center": "2600",
            },
        ]
    )


def _decisions_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trial_number": "IPR2022-LABEL_BY_FWD_1",
                "decision_type": "Decision",
                "document_type": "Final Written Decision:  original",
                "decision_issue_date": date(2023, 5, 23),
                "trial_outcome": "All Challenged Claims Unpatentable",
            }
        ]
    )


def _petitions_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trial_number": "IPR2022-LABEL_BY_STATUS_0",
                "petition_pdf_uri": "https://example/p1.pdf",
                "petition_filing_date_doc": date(2022, 1, 15),
            },
            {
                "trial_number": "IPR2022-LABEL_BY_FWD_1",
                "petition_pdf_uri": "https://example/p2.pdf",
                "petition_filing_date_doc": date(2022, 5, 23),
            },
        ]
    )


def _petition_quarantine_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [{"trial_number": "IPR2022-QUARANTINED", "reason": "picker_no_match"}]
    )


def _patent_quarantine_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["application_number", "reason"])


@pytest.fixture
def patent_payloads(monkeypatch) -> dict[str, dict]:
    payloads: dict[str, dict] = {
        "14000001": {
            "applicationMetaData": {"cpcClassificationBag": ["G06F 17/00"]},
            "eventDataBag": [
                {"eventCode": "CTNF", "eventDate": "2017-01-01"},
                {"eventCode": "TRIALFWD", "eventDate": "2024-01-01"},
            ],
        },
        "14000002": {
            "applicationMetaData": {"cpcClassificationBag": ["H04L 9/00"]},
            "eventDataBag": [
                {"eventCode": "CTNF", "eventDate": "2016-06-01"},
                {"eventCode": "WIDS", "eventDate": "2017-08-01"},
            ],
        },
    }

    def load_if_present(bucket, key):
        assert bucket is Stage.PATENTS
        if key not in payloads:
            return None
        return {"patentFileWrapperDataBag": [payloads[key]]}

    monkeypatch.setattr("ml_uspto.parse.joiner.local.load_if_present", load_if_present)
    return payloads


def test_join_all_excludes_petition_quarantine(patent_payloads):
    df, report = join_all(
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        petition_quarantine=_petition_quarantine_frame(),
        patent_quarantine=_patent_quarantine_frame(),
    )

    assert set(df["trial_number"]) == {"IPR2022-LABEL_BY_STATUS_0", "IPR2022-LABEL_BY_FWD_1"}
    assert report.n_trials_input == 3
    assert report.n_petition_quarantine == 1
    assert report.n_joined == 2


def test_join_all_assigns_correct_labels(patent_payloads):
    df, _ = join_all(
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        petition_quarantine=_petition_quarantine_frame(),
        patent_quarantine=_patent_quarantine_frame(),
    )

    by_trial = df.set_index("trial_number")["cancelled"].to_dict()
    assert by_trial["IPR2022-LABEL_BY_STATUS_0"] == 0
    assert by_trial["IPR2022-LABEL_BY_FWD_1"] == 1


def test_join_all_aggregates_patent_features_with_t0(patent_payloads):
    df, report = join_all(
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        petition_quarantine=_petition_quarantine_frame(),
        patent_quarantine=_patent_quarantine_frame(),
    )

    by_trial = df.set_index("trial_number")
    fwd = by_trial.loc["IPR2022-LABEL_BY_FWD_1"]
    # Both events are pre-T0 (2022-05-23) and non-banned.
    assert fwd["n_events_pre_t0"] == 2
    assert fwd["n_ex_pre_t0"] == 1  # CTNF
    assert fwd["n_aa_pre_t0"] == 1  # WIDS
    assert fwd["cpc_section"] == "H"
    # days_grant_to_petition = (2022-05-23) - (2017-01-01) = 1968 days.
    assert fwd["days_grant_to_petition"] == (date(2022, 5, 23) - date(2017, 1, 1)).days
    assert report.n_with_patent_features == 2


def test_join_all_marks_quarantined_apps_without_features(patent_payloads):
    patent_qn = pd.DataFrame(
        [{"application_number": "14000002", "reason": "http_error"}]
    )
    df, report = join_all(
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        petition_quarantine=_petition_quarantine_frame(),
        patent_quarantine=patent_qn,
    )

    fwd = df.set_index("trial_number").loc["IPR2022-LABEL_BY_FWD_1"]
    assert pd.isna(fwd["n_events_pre_t0"])
    assert pd.isna(fwd["cpc_section"])
    assert report.n_patent_quarantine == 1
    assert report.n_with_patent_features == 1


def test_join_all_handles_missing_cache(monkeypatch):
    monkeypatch.setattr(
        "ml_uspto.parse.joiner.local.load_if_present", lambda bucket, key: None
    )
    df, report = join_all(
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        petition_quarantine=_petition_quarantine_frame(),
        patent_quarantine=_patent_quarantine_frame(),
    )

    assert report.n_with_patent_features == 0
    assert report.n_without_patent_features == len(df)
    assert df["n_events_pre_t0"].isna().all()
