"""Pin `models.preprocessing.build_preprocessor` — the train-fit-only encoders.

The whole point of this preprocessor is leakage discipline:
  - frequencies must come from train rows only;
  - the OHE column set must be frozen at fit time so unseen test
    categories cannot expand the matrix;
  - the median imputer must use the train median.

These tests construct deliberately asymmetric train/test frames and
assert the boundary holds.
"""

import numpy as np
import pandas as pd

from ml_uspto.models.preprocessing import (
    FrequencyEncoder,
    build_preprocessor,
)


def _frame(
    petitioners: list[str],
    technology_centers: list[str],
    cpc_sections: list[str],
    n_events: list[float],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "petitioner_real_party": petitioners,
            "owner_real_party": ["O"] * len(petitioners),
            "technology_center": technology_centers,
            "cpc_section": cpc_sections,
            "n_events": n_events,
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
    pre.fit(train)
    out = pre.transform(test)
    columns = pre.get_feature_names_out()

    # Train saw TC ∈ {2400, 3700} and CPC ∈ {G, H}. The unseen test
    # rows must not introduce tc_9999 / cpc_Y columns.
    assert "technology_center_9999" not in columns
    assert "cpc_section_Y" not in columns
    # And the unseen rows ride out as all-zero across the OHE block.
    ohe_idx = [
        i for i, c in enumerate(columns)
        if c.startswith("technology_center_") or c.startswith("cpc_section_")
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
    pre.fit(train)
    out = pre.transform(test)
    columns = pre.get_feature_names_out()

    j = list(columns).index("n_events")
    assert out[0, j] == 30.0
