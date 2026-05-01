"""Tests for `parse.joiner.join_all` label resolution and frame assembly."""
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.parse.joiner import join_all


def _trials_frame() -> pd.DataFrame:
    """Three trials covering each label-resolution path.

    - LABEL_BY_STATUS_0 -> trial_status = "Institution Denied"
    - LABEL_BY_FWD_1    -> trial_status = "Final Written Decision" + decisions FWD
                           with trialOutcome = "All Challenged Claims Unpatentable"
    - NO_PETITION       -> labeled but no petition row -> dropped by inner-join
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


def _patents_frame() -> pd.DataFrame:
    """Two patents matching the trials' application numbers with parallel-array cols."""
    return pd.DataFrame(
        [
            {
                "application_number": "14000001",
                "filing_date": date(2017, 1, 1),
                "cpc_codes": ["G06F 17/00"],
                "event_codes": ["CTNF"],
                "event_dates": [date(2017, 6, 1)],
                "assignment_received_dates": [],
                "assignment_recorded_dates": [],
                "assignees_per_assignment": [],
                "parent_app_numbers": [],
            },
            {
                "application_number": "14000002",
                "filing_date": date(2016, 1, 1),
                "cpc_codes": ["H04L 9/00"],
                "event_codes": ["CTNF", "WIDS"],
                "event_dates": [date(2016, 6, 1), date(2017, 8, 1)],
                "assignment_received_dates": [],
                "assignment_recorded_dates": [],
                "assignees_per_assignment": [],
                "parent_app_numbers": [],
            },
        ]
    )


@pytest.fixture
def empty_storage(tmp_path: Path) -> LocalStorage:
    """LocalStorage rooted at a fresh tmp_path with no seeded objects."""
    return LocalStorage(raw_root=tmp_path / "raw", processed_root=tmp_path / "processed")


def test_join_all_drops_labeled_trials_without_petition_row(empty_storage):
    """Labeled trials missing a petition row are dropped by the inner join."""
    df, report = join_all(
        empty_storage,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        patents=_patents_frame(),
    )

    assert set(df["trial_number"]) == {"IPR2022-LABEL_BY_STATUS_0", "IPR2022-LABEL_BY_FWD_1"}
    assert report.n_trials_input == 3
    assert report.n_joined == 2
    assert report.n_trials_labeled - report.n_joined == 1


def test_join_all_assigns_correct_labels(empty_storage):
    """Status-resolved and FWD-resolved trials receive the expected cancelled labels."""
    df, _ = join_all(
        empty_storage,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        patents=_patents_frame(),
    )

    by_trial = df.set_index("trial_number")["cancelled"].to_dict()
    assert by_trial["IPR2022-LABEL_BY_STATUS_0"] == 0
    assert by_trial["IPR2022-LABEL_BY_FWD_1"] == 1


def test_join_all_attaches_patent_arrays(empty_storage):
    """Joined frame carries the patent parallel-array cols for the features stage."""
    df, _ = join_all(
        empty_storage,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        patents=_patents_frame(),
    )
    by_trial = df.set_index("trial_number")
    fwd = by_trial.loc["IPR2022-LABEL_BY_FWD_1"]
    assert list(fwd["event_codes"]) == ["CTNF", "WIDS"]
    assert list(fwd["cpc_codes"]) == ["H04L 9/00"]


def test_join_all_left_joins_missing_patents(empty_storage):
    """Left-join trials whose patent fetch failed.

    Trials whose patent isn't in `patents.parquet` survive the join with NaN
    for every patent column.
    """
    patents_partial = _patents_frame().iloc[:1]  # only IPR2022-LABEL_BY_STATUS_0's app
    df, report = join_all(
        empty_storage,
        trials=_trials_frame(),
        decisions=_decisions_frame(),
        petitions=_petitions_frame(),
        patents=patents_partial,
    )
    assert report.n_joined == 2
    fwd = df.set_index("trial_number").loc["IPR2022-LABEL_BY_FWD_1"]
    assert pd.isna(fwd["filing_date"])  # patent scalars NaN
    # list cols come back as NaN/None for missing left-join rows
    assert fwd.get("event_codes") is None or (
        isinstance(fwd["event_codes"], float) and pd.isna(fwd["event_codes"])
    )


def test_join_warns_on_t0_mismatch_proceedings_wins(empty_storage, caplog):
    """Warn on petition T0 mismatches and keep proceedings as canonical."""
    petitions = _petitions_frame(fwd_doc_date=date(2022, 5, 28))
    with caplog.at_level(logging.WARNING, logger="ml_uspto.parse.joiner"):
        df, report = join_all(
            empty_storage,
            trials=_trials_frame(),
            decisions=_decisions_frame(),
            petitions=petitions,
            patents=_patents_frame(),
        )

    assert report.n_petition_t0_mismatch == 1
    assert any("T₀ mismatch" in m for m in caplog.messages)
    fwd = df.set_index("trial_number").loc["IPR2022-LABEL_BY_FWD_1"]
    assert fwd["petition_filing_date"] == date(2022, 5, 23)


def test_join_no_warning_when_t0_matches(empty_storage, caplog):
    """Avoid mismatch warnings when petition and proceeding T0 agree."""
    with caplog.at_level(logging.WARNING, logger="ml_uspto.parse.joiner"):
        _, report = join_all(
            empty_storage,
            trials=_trials_frame(),
            decisions=_decisions_frame(),
            petitions=_petitions_frame(),
            patents=_patents_frame(),
        )
    assert report.n_petition_t0_mismatch == 0
    assert not any("T₀ mismatch" in m for m in caplog.messages)
