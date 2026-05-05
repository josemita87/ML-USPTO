"""Smoke tests for `models.tune.grid_search_cv`.

The serious leakage guarantees are pinned in `test_preprocessing.py`;
these tests just verify the grid search wrapper itself: parameter
fan-out, correct best-combo selection, and that the LR estimator
(itself a Pipeline) accepts the `lr__*` parameter prefix in the grid.
"""

import numpy as np
import pandas as pd

from ml_uspto.models.schemas.enums import ModelName
from ml_uspto.models.tune import _iter_param_combos, grid_search_cv


def _toy_frame(n: int, seed: int = 0) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Build a tiny preprocessor-compatible frame for grid-search smoke tests."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2018-01-01", periods=n, freq="D")
    return (
        pd.DataFrame(
            {
                "petitioner_real_party": rng.choice(["A", "B", "C"], size=n),
                "owner_real_party": rng.choice(["X", "Y"], size=n),
                "technology_center": rng.choice(["2100", "2400"], size=n),
                "cpc_section": rng.choice(["G", "H"], size=n),
                "ptab_era": ["iancu_fintiv"] * n,
                "entity_size": ["Regular Undiscounted"] * n,
                "inventor_geo": ["us_only"] * n,
                "n_events": rng.integers(0, 50, size=n).astype(float),
                "petition_filing_date": dates,
                "label_resolution_date": dates,
                "patent_number": [f"P{i}" for i in range(n)],
            }
        ),
        pd.Series(rng.integers(0, 2, size=n)),
        pd.Series(dates),
    )


def test_iter_param_combos_cartesian_product():
    """Cartesian product yields one dict per (a, b) pair."""
    grid = {"a": [1, 2], "b": ["x", "y", "z"]}
    combos = _iter_param_combos(grid)
    assert len(combos) == 6
    assert {"a": 1, "b": "x"} in combos
    assert {"a": 2, "b": "z"} in combos


def test_iter_param_combos_empty_grid():
    """Empty grid yields a single empty-combo dict so callers always iterate at least once."""
    assert _iter_param_combos({}) == [{}]


def test_grid_search_rf_returns_best_combo():
    """`best_params` is the argmax of `mean_roc_auc` across all entries."""
    X, y, dates = _toy_frame(120)
    grid = {"n_estimators": [10, 20], "max_depth": [4, 8]}
    result = grid_search_cv(
        X, y, dates, ModelName.RANDOM_FOREST, grid=grid, cv_folds=3
    )
    assert len(result.entries) == 4
    assert result.best_params in [e.params for e in result.entries]
    best_auc = max(e.mean_roc_auc for e in result.entries)
    assert result.best_mean_roc_auc == best_auc


def test_grid_search_logistic_accepts_lr_prefix():
    """`lr__C` routes through the inner Pipeline's `set_params` correctly."""
    X, y, dates = _toy_frame(120)
    grid = {"lr__C": [0.1, 1.0]}
    result = grid_search_cv(
        X, y, dates, ModelName.LOGISTIC, grid=grid, cv_folds=3
    )
    assert len(result.entries) == 2
    assert {"lr__C": 0.1} in [e.params for e in result.entries]
