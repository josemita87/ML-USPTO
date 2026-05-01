"""Pin `features.transforms.build_features` — the single feature-engineering
pipeline that turns the joined frame into a model-ready matrix.

Two layers:
  1. Per-row T₀-leakage filter + count/span aggregation on the patent
     parallel-array columns (`event_codes`, `event_dates`,
     `assignment_received_dates`, `assignees_per_assignment`, …).
  2. Static-feature transforms (filing-date breakdown, one-hots,
     frequency encoding, regime indicators, imputation).

The fixture mirrors the joined-frame shape that `parse.joiner.join_all`
emits — proceedings/petition cols + the patent parallel arrays.
"""

from datetime import date

import pandas as pd

from ml_uspto.features.transforms import build_features


def _joined_frame() -> pd.DataFrame:
    """One trial whose patent has a mix of pre-T₀, post-T₀, TRIAL, and
    banned-prefix events plus pre/post-T₀ assignments.

    T₀ = 2022-05-23. Pre-T₀ keepers (5): IEXX, CTNF, WIDS, M2551, ZZZZ.
    Dropped: TRIALPET (banned prefix), TRIALFWD (banned prefix),
    CTFR (post-T₀).
    """
    return pd.DataFrame(
        [
            {
                "trial_number": "IPR2022-01002",
                "cancelled": 1,
                "petition_filing_date": date(2022, 5, 23),
                "patent_number": "US10000000",
                "application_number": "14709428",
                "grant_date": None,  # exercises days_grant_to_petition = None
                "technology_center": "2400",
                "group_art_unit": "2467",
                "petitioner_real_party": "Acme",
                "owner_real_party": "Globex",
                # Patent parallel-array cols
                "filing_date": date(2014, 1, 1),
                "cpc_codes": ["H04W 88/06", "G06F 17/00"],
                "event_codes": ["IEXX", "CTNF", "WIDS", "M2551", "ZZZZ", "TRIALPET", "TRIALFWD", "CTFR"],
                "event_dates": [
                    date(2015, 5, 11),
                    date(2016, 1, 1),
                    date(2016, 2, 1),
                    date(2020, 1, 1),
                    date(2021, 1, 1),
                    date(2022, 5, 23),
                    date(2024, 1, 1),
                    date(2023, 1, 1),
                ],
                "assignment_received_dates": [date(2018, 1, 1), date(2022, 6, 1)],
                "assignment_recorded_dates": [date(2018, 1, 5), None],
                "assignees_per_assignment": [
                    ["Apple Inc.", "APPLE INC"],   # dedup → 1 distinct
                    ["Post T0 LLC"],                # post-T₀ → drop entirely
                ],
                "parent_app_numbers": ["11111111", "22222222"],
            },
        ]
    )


def test_t0_filter_drops_post_t0_trial_and_banned_events():
    """Per-row T₀ filter: events with date ≥ T₀, TRIAL-prefix codes,
    and banned-category codes are excluded from the counts.
    """
    features = build_features(_joined_frame())

    # 5 surviving events: IEXX (PE), CTNF (EX), WIDS (AA), M2551 (MAINT), ZZZZ (OTHER).
    assert features["n_events_pre_t0"].iloc[0] == 5
    assert features["n_pe_pre_t0"].iloc[0] == 1
    assert features["n_ex_pre_t0"].iloc[0] == 1
    assert features["n_aa_pre_t0"].iloc[0] == 1
    assert features["n_maint_pre_t0"].iloc[0] == 1
    assert features["n_other_pre_t0"].iloc[0] == 1
    assert features["n_office_actions"].iloc[0] == 1  # alias for EX


def test_assignments_drop_post_t0_and_dedup_assignees():
    """Assignments require any of received/recorded < T₀. Pre-T₀
    assignment's two assignee strings normalize to the same key → 1 distinct.
    Post-T₀ assignment is dropped entirely.
    """
    features = build_features(_joined_frame())

    assert features["n_assignments_pre_t0"].iloc[0] == 1
    assert features["n_distinct_assignees_pre_t0"].iloc[0] == 1
    # Earliest pre-T₀ date for the surviving assignment is 2018-01-01.
    assert features["days_since_last_assignment"].iloc[0] == (
        date(2022, 5, 23) - date(2018, 1, 1)
    ).days


def test_prosecution_span_uses_only_pre_t0_events():
    """Span = max - min over the kept pre-T₀ event dates."""
    features = build_features(_joined_frame())
    # Kept event dates: 2015-05-11 .. 2021-01-01.
    assert features["prosecution_span_days"].iloc[0] == (
        date(2021, 1, 1) - date(2015, 5, 11)
    ).days


def test_grant_date_none_yields_grant_to_petition_imputed():
    """When `grant_date` is None, `days_grant_to_petition` starts NaN
    and gets median-filled on this single-row fixture (median of NaN = NaN
    → final fillna(0))."""
    features = build_features(_joined_frame())
    # Single row + no grant → median over NaN is NaN; final fillna(0) hits it.
    assert features["days_grant_to_petition"].iloc[0] == 0
    # Paired indicator flags the missing.
    assert features["days_grant_to_petition_missing"].iloc[0] == 1


def test_cpc_section_one_hot_picks_first_letter():
    features = build_features(_joined_frame())
    assert features["cpc_H"].iloc[0] == 1


def test_no_recorded_assignment_regime_indicator():
    df = _joined_frame()
    # Strip all assignments to trigger the regime indicator.
    df.at[0, "assignment_received_dates"] = []
    df.at[0, "assignment_recorded_dates"] = []
    df.at[0, "assignees_per_assignment"] = []
    features = build_features(df)
    assert features["no_recorded_assignment"].iloc[0] == 1
    assert features["n_assignments_pre_t0"].iloc[0] == 0
