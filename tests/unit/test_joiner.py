import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.joiner import join_all


def _trials_frame() -> pd.DataFrame:
    """Three trials covering each label-resolution path:
      - LABEL_BY_STATUS_0 → trial_status = "Institution Denied"
      - LABEL_BY_FWD_1    → trial_status = "Final Written Decision" + decisions FWD
                            with trialOutcome = "All Challenged Claims Unpatentable"
      - NO_PETITION       → labeled but no petition row → dropped by inner-join
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
                "trial_number": "IPR2022-NO_PETITION",
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
    # `document_title` carries the granular outcome empirically; the
    # bare API field `trial_outcome` is always "Final Written Decision"
    # (see config/labels.yaml). The title-regex layer in
    # parse.labels picks this up.
    return pd.DataFrame(
        [
            {
                "trial_number": "IPR2022-LABEL_BY_FWD_1",
                "decision_type": "Decision",
                "document_type": "Final Written Decision:  original",
                "decision_issue_date": date(2023, 5, 23),
                "trial_outcome": "Final Written Decision",
                "document_title": (
                    "Final Written Decision Determining All Challenged Claims "
                    "Unpatentable 35 U.S.C. § 318(a)"
                ),
                "document_identifier": "999999999",
            }
        ]
    )


def _petitions_frame(fwd_doc_date: date = date(2022, 5, 23)) -> pd.DataFrame:
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
                "petition_filing_date_doc": fwd_doc_date,
            },
        ]
    )


@pytest.fixture
def storage_with_payloads(tmp_path: Path) -> LocalStorage:
    """LocalStorage seeded with two patent payloads under bucket=Stage.PATENTS."""
    storage = LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")
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
    for app, record in payloads.items():
        storage.save_object(Stage.PATENTS, app, {"patentFileWrapperDataBag": [record]})
    return storage


def test_join_all_drops_labeled_trials_without_petition_row(storage_with_payloads):
    """A labeled trial with no row in `petitions` is excluded from the
    joined frame via inner-join. `n_trials_labeled − n_joined` captures
    the count.
    """
    df, report = join_all(
        storage_with_payloads,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
    )

    assert set(df["trial_number"]) == {"IPR2022-LABEL_BY_STATUS_0", "IPR2022-LABEL_BY_FWD_1"}
    assert report.n_trials_input == 3
    assert report.n_joined == 2
    assert report.n_trials_labeled - report.n_joined == 1


def test_join_all_assigns_correct_labels(storage_with_payloads):
    df, _ = join_all(
        storage_with_payloads,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
    )

    by_trial = df.set_index("trial_number")["cancelled"].to_dict()
    assert by_trial["IPR2022-LABEL_BY_STATUS_0"] == 0
    assert by_trial["IPR2022-LABEL_BY_FWD_1"] == 1


def test_join_all_aggregates_patent_features_with_t0(storage_with_payloads):
    df, report = join_all(
        storage_with_payloads,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
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


def test_join_all_handles_missing_cache(tmp_path: Path):
    empty = LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")
    df, report = join_all(
        empty,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
    )

    assert report.n_with_patent_features == 0
    assert report.n_joined - report.n_with_patent_features == len(df)
    assert df["n_events_pre_t0"].isna().all()


def test_join_warns_on_t0_mismatch_proceedings_wins(storage_with_payloads, caplog):
    """When `petition_filing_date` (proceedings) != `petition_filing_date_doc`
    (documents), the joiner logs a warning and the proceedings-side T₀
    drives the patent aggregation.
    """
    # Diverges from proceedings (2022-05-23) by 5 days.
    petitions = _petitions_frame(fwd_doc_date=date(2022, 5, 28))
    with caplog.at_level(logging.WARNING, logger="ml_uspto.parse.joiner"):
        df, report = join_all(
            storage_with_payloads,
            trials=_trials_frame(),
            decisions=_decisions_frame(),
            petitions=petitions,
        )

    assert report.n_petition_t0_mismatch == 1
    assert any("T₀ mismatch" in m for m in caplog.messages)
    # Proceedings-side T₀ drives the days_grant_to_petition arithmetic.
    fwd = df.set_index("trial_number").loc["IPR2022-LABEL_BY_FWD_1"]
    assert fwd["days_grant_to_petition"] == (date(2022, 5, 23) - date(2017, 1, 1)).days


def test_join_no_warning_when_t0_matches(storage_with_payloads, caplog):
    with caplog.at_level(logging.WARNING, logger="ml_uspto.parse.joiner"):
        _, report = join_all(
            storage_with_payloads,
            trials=_trials_frame(),
            decisions=_decisions_frame(),
            petitions=_petitions_frame(),
        )
    assert report.n_petition_t0_mismatch == 0
    assert not any("T₀ mismatch" in m for m in caplog.messages)
