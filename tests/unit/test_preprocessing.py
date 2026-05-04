"""Pin `models.preprocessing.build_preprocessor` — the train-fit-only encoders.

The whole point of this preprocessor is leakage discipline:
  - frequencies must come from train rows only;
  - the OHE column set must be frozen at fit time so unseen test
    categories cannot expand the matrix;
  - the median imputer must use the train median;
  - the per-group cancellation priors must use only training rows
    with `petition_filing_date < this_row's_T₀` (strict).

These tests construct deliberately asymmetric train/test frames and
assert the boundary holds.
"""

import numpy as np
import pandas as pd
import pytest

from ml_uspto.models.preprocessing import (
    FrequencyEncoder,
    PriorEncoder,
    build_preprocessor,
)


def _frame(
    petitioners: list[str],
    technology_centers: list[str],
    cpc_sections: list[str],
    n_events: list[float],
    *,
    dates: list[str] | None = None,
    patent_numbers: list[str] | None = None,
    ptab_eras: list[str] | None = None,
) -> pd.DataFrame:
    n = len(petitioners)
    return pd.DataFrame(
        {
            "petitioner_real_party": petitioners,
            "owner_real_party": ["O"] * n,
            "technology_center": technology_centers,
            "cpc_section": cpc_sections,
            "ptab_era": ptab_eras if ptab_eras is not None else ["iancu_fintiv"] * n,
            "n_events": n_events,
            "petition_filing_date": pd.to_datetime(
                dates if dates is not None else ["2020-01-01"] * n
            ),
            "patent_number": patent_numbers if patent_numbers is not None else [f"P{i}" for i in range(n)],
        }
    )


def test_frequency_encoder_fits_on_train_only():
    """Counts come from the train fit; unseen test categories map to 0."""
    train = pd.DataFrame({"petitioner_real_party": ["A", "A", "A", "B"]})
    test = pd.DataFrame({"petitioner_real_party": ["A", "B", "C"]})

    enc = FrequencyEncoder().fit(train)
    out = enc.transform(test)

    assert out.tolist() == [[3], [1], [0]]


def test_preprocessor_one_hot_freezes_columns_at_fit():
    """Categories present only in test produce no new OHE columns."""
    train = _frame(
        petitioners=["A", "A", "B"],
        technology_centers=["2400", "2400", "3700"],
        cpc_sections=["H", "G", "H"],
        n_events=[10, 20, 30],
    )
    test = _frame(
        petitioners=["A", "Z"],          # Z unseen
        technology_centers=["2400", "9999"],  # 9999 unseen
        cpc_sections=["H", "Y"],         # Y unseen
        n_events=[5, 5],
    )

    pre = build_preprocessor()
    pre.fit(train, y=pd.Series([0, 1, 0]))
    out = pre.transform(test)
    columns = pre.get_feature_names_out()

    # Train saw TC ∈ {2400, 3700} and CPC ∈ {G, H}. The unseen test
    # rows must not introduce tc_9999 / cpc_Y columns.
    assert "technology_center_9999" not in columns
    assert "cpc_section_Y" not in columns
    # And the unseen rows ride out as all-zero across the OHE block.
    # The PriorEncoder branches also emit `<group>_prior_rate/_count`
    # columns — those are NOT one-hot and must not be summed here.
    prior_suffixes = ("_prior_rate", "_prior_count")
    ohe_idx = [
        i for i, c in enumerate(columns)
        if (c.startswith("technology_center_") or c.startswith("cpc_section_"))
        and not c.endswith(prior_suffixes)
    ]
    assert out[1, ohe_idx].sum() == 0  # 9999 + Y row contributes nothing


def test_preprocessor_median_imputer_uses_train_median():
    """NaNs at transform time fill with the train median, not the test median."""
    train = _frame(
        petitioners=["A"] * 5,
        technology_centers=["2400"] * 5,
        cpc_sections=["H"] * 5,
        n_events=[10.0, 20.0, 30.0, 40.0, 50.0],  # train median = 30
    )
    test = _frame(
        petitioners=["A"],
        technology_centers=["2400"],
        cpc_sections=["H"],
        n_events=[np.nan],  # test-only row, must impute from train
    )

    pre = build_preprocessor()
    pre.fit(train, y=pd.Series([0, 1, 0, 1, 0]))
    out = pre.transform(test)
    columns = pre.get_feature_names_out()

    j = list(columns).index("n_events")
    assert out[0, j] == 30.0


def test_prior_encoder_strict_t0_filter_and_count():
    """A row's prior excludes training rows whose T₀ equals or exceeds its own T₀."""
    train = pd.DataFrame(
        {
            "petitioner_real_party": ["A", "A", "A", "B", "B"],
            "petition_filing_date": pd.to_datetime(
                ["2020-01-01", "2020-06-01", "2021-01-01", "2020-06-01", "2021-06-01"]
            ),
        }
    )
    y_train = pd.Series([1, 0, 1, 0, 1])
    test = pd.DataFrame(
        {
            "petitioner_real_party": ["A", "A", "B", "C"],
            # row 0: same T₀ as a train row → strict < excludes it (count=0 → fallback)
            # row 1: only train rows with T₀ < this date count
            # row 2: B has 1 prior at 2020-06-01 (cancelled=0) → rate 0, count 1
            # row 3: unseen petitioner → fallback
            "petition_filing_date": pd.to_datetime(
                ["2020-01-01", "2020-12-01", "2021-01-01", "2021-06-01"]
            ),
        }
    )

    enc = PriorEncoder(group_columns=["petitioner_real_party"]).fit(train, y=y_train)
    out = enc.transform(test)

    # Output: [[rate, count], ...]
    rate_a_2020_01 = out[0, 0]
    count_a_2020_01 = out[0, 1]
    # No A-rows strictly before 2020-01-01 → fallback rate, count 0.
    assert count_a_2020_01 == 0

    # A has 2 rows (2020-01-01 cancelled=1, 2020-06-01 cancelled=0) before 2020-12-01.
    rate_a_2020_12 = out[1, 0]
    count_a_2020_12 = out[1, 1]
    assert count_a_2020_12 == 2
    assert rate_a_2020_12 == pytest.approx(0.5)

    # B has 1 row (2020-06-01 cancelled=0) before 2021-01-01.
    rate_b_2021_01 = out[2, 0]
    count_b_2021_01 = out[2, 1]
    assert count_b_2021_01 == 1
    assert rate_b_2021_01 == pytest.approx(0.0)

    # Unseen petitioner C → count 0, rate is the global rolling rate at 2021-06-01.
    # Strict T₀ < T₀ excludes the train row whose date *equals* 2021-06-01
    # (the last B row). 4 train rows are strictly before; their cancelled
    # values are [1, 0, 0, 1] → rate 0.5.
    rate_c = out[3, 0]
    count_c = out[3, 1]
    assert count_c == 0
    assert rate_c == pytest.approx(0.5)


def test_prior_encoder_composite_key():
    """Tuple group keys (e.g. petitioner-owner pair) match exact tuple, not either column."""
    train = pd.DataFrame(
        {
            "petitioner_real_party": ["A", "A", "B"],
            "owner_real_party":      ["X", "Y", "X"],
            "petition_filing_date":  pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01"]),
        }
    )
    y_train = pd.Series([1, 0, 0])
    # Row 0 should match (A,X) — only 1 train row with that pair (cancelled=1).
    # Row 1 should match (A,Y) — only 1 train row with that pair (cancelled=0).
    test = pd.DataFrame(
        {
            "petitioner_real_party": ["A", "A"],
            "owner_real_party":      ["X", "Y"],
            "petition_filing_date":  pd.to_datetime(["2021-01-01", "2021-01-01"]),
        }
    )
    enc = PriorEncoder(
        group_columns=["petitioner_real_party", "owner_real_party"]
    ).fit(train, y=y_train)
    out = enc.transform(test)

    assert out[0, 1] == 1 and out[0, 0] == pytest.approx(1.0)
    assert out[1, 1] == 1 and out[1, 0] == pytest.approx(0.0)
