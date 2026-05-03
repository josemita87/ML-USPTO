"""Pin `features.transforms.build_features`, the leakage-free intermediate builder.

Two layers exercised here:
  1. Per-row T₀-leakage filter + count/span aggregation on the patent
     parallel-array columns (`event_codes`, `event_dates`,
     `assignment_received_dates`, `assignees_per_assignment`, …).
  2. Row-local static transforms (filing-date breakdown, art-unit
     prefix, regime indicators) and pass-through of raw categoricals
     (`technology_center`, `cpc_section`, `petitioner_real_party`,
     `owner_real_party`) — encoded downstream by the modeling-side
     preprocessor.

The fixture mirrors the joined-frame shape that `parse.joiner.join_all`
emits — proceedings/petition cols + the patent parallel arrays.
"""

from datetime import date

import pandas as pd

from ml_uspto.features.transforms import build_features


def _joined_frame() -> pd.DataFrame:
    """Build a single-trial joined frame exercising every T0-filter branch.

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
                "event_codes": [
                    "IEXX",
                    "CTNF",
                    "WIDS",
                    "M2551",
                    "ZZZZ",
                    "TRIALPET",
                    "TRIALFWD",
                    "CTFR",
                ],
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
    """Post-T0, TRIAL-prefixed, and banned-category events are excluded from counts."""
    features = build_features(_joined_frame())

    # 5 surviving events: IEXX (PE), CTNF (EX), WIDS (AA), M2551 (MAINT), ZZZZ (OTHER).
    assert features["n_events_pre_t0"].iloc[0] == 5
    assert features["n_pe_pre_t0"].iloc[0] == 1
    assert features["n_ex_pre_t0"].iloc[0] == 1
    assert features["n_aa_pre_t0"].iloc[0] == 1
    assert features["n_maint_pre_t0"].iloc[0] == 1
    assert features["n_other_pre_t0"].iloc[0] == 1


def test_assignments_drop_post_t0_and_dedup_assignees():
    """Post-T0 assignments drop and pre-T0 assignee variants normalize to one key."""
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


def test_grant_date_none_yields_nan_with_missing_flag():
    """Missing grant_date leaves days_grant_to_petition NaN and sets the paired indicator.

    Imputation lives in the modeling-side preprocessor, so the
    intermediate frame must surface the NaN for `SimpleImputer` to fill
    on training rows only.
    """
    features = build_features(_joined_frame())
    assert pd.isna(features["days_grant_to_petition"].iloc[0])
    assert features["days_grant_to_petition_missing"].iloc[0] == 1


def test_cpc_section_passes_through_first_letter():
    """CPC section is exposed as the section letter; one-hot is downstream."""
    features = build_features(_joined_frame())
    assert features["cpc_section"].iloc[0] == "H"


def test_raw_categoricals_passed_through_unencoded():
    """Party identifiers and TC are kept raw for the modeling preprocessor."""
    features = build_features(_joined_frame())
    assert features["technology_center"].iloc[0] == "2400"
    assert features["petitioner_real_party"].iloc[0] == "Acme"
    assert features["owner_real_party"].iloc[0] == "Globex"
    # Frequency / one-hot columns must NOT be emitted by build_features —
    # otherwise we'd be back to corpus-level leakage.
    for col in ("petitioner_frequency", "owner_frequency", "tc_2400", "cpc_H"):
        assert col not in features.columns


def test_no_recorded_assignment_regime_indicator():
    """Stripping all assignments raises the no_recorded_assignment regime flag."""
    df = _joined_frame()
    # Strip all assignments to trigger the regime indicator.
    df.at[0, "assignment_received_dates"] = []
    df.at[0, "assignment_recorded_dates"] = []
    df.at[0, "assignees_per_assignment"] = []
    features = build_features(df)
    assert features["no_recorded_assignment"].iloc[0] == 1
    assert features["n_assignments_pre_t0"].iloc[0] == 0
