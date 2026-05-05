"""Constants for `ml_uspto.models`."""

from collections.abc import Callable
from typing import Any

from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ml_uspto.models.schemas.enums import ModelName

MODELS: dict[ModelName, Callable[[], Any]] = {
    # Sweep on the enriched feature matrix (entity_size, inventor_geo,
    # petition_text_length, n_cpc_codes, n_cpc_subclasses) picked these
    # knobs as the held-out winner (HO AUC 0.6234 on the 2023+ tail,
    # mature_days=540) at n_estimators=800. A follow-up trim to 500 trees
    # came in at the same held-out AUC within fold-level noise (±0.034
    # 5-fold std) for ~40% lower fit cost and is what shipped in
    # final_submission/report.pdf; the original grid in `MODEL_GRIDS`
    # below still spans 800/1500 since 500 was the result of a post-grid
    # ablation rather than a grid-search winner. Notable: `max_depth=10`
    # cap underfits badly (HO 0.575); removing it is the single biggest
    # lever. `max_features=0.3` lets each split see ~25 of the ~85
    # post-preprocessing columns — `sqrt` is too restrictive for this
    # signal-poor matrix where many weak features need to compose.
    # `min_samples_leaf=5` damps the variance-floor RF would otherwise
    # hit on rare counsel/party priors.
    ModelName.RANDOM_FOREST: lambda: RandomForestClassifier(
        n_estimators=500,
        max_depth=None,
        max_features=0.3,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    ),
    # Scaler is mandatory: post-preprocessor magnitudes mix prior counts,
    # frequency encodings, and binary OHE — unscaled L2 follows the largest.
    ModelName.LOGISTIC: lambda: Pipeline(
        steps=[
            ("scale", StandardScaler()),
            (
                "lr",
                LogisticRegression(
                    solver="saga",
                    class_weight="balanced",
                    max_iter=5000,
                    random_state=42,
                ),
            ),
        ]
    ),
}

# Keys are sklearn `set_params` paths; LR uses `lr__` because its estimator is a Pipeline.
MODEL_GRIDS: dict[ModelName, dict[str, list[Any]]] = {
    # 24-combo grid spanning the winner (n_est=800, depth=20, max_feat=0.3,
    # leaf=2) plus distinctly different alternatives on each axis: a shallower
    # cap (depth=10), a much larger forest (1500), the sklearn-default
    # max_features="sqrt", and a heavier leaf regularizer (5).
    ModelName.RANDOM_FOREST: {
        "n_estimators": [800, 1500],
        "max_depth": [10, 20, None],
        "max_features": [0.3, "sqrt"],
        "min_samples_leaf": [2, 5],
    },
    ModelName.LOGISTIC: {
        "lr__C": [0.01, 0.1, 1.0, 10.0],
        "lr__l1_ratio": [0.0, 1.0],
    },
}

# Sentinel inserted by `preprocessing.build_preprocessor`'s OHE branch
# in place of NaN for categorical columns, so missingness becomes an
# explicit one-hot level rather than collapsing into all-zeros.
MISSING_CATEGORY_SENTINEL: str = "__missing__"

# Group keys for `preprocessing.PriorEncoder`. Each tuple becomes one
# prior branch in `build_preprocessor`, contributing two output features
# (`<label>_prior_rate`, `<label>_prior_count`) per group. The five
# groups below mirror the empirically validated repeat-player /
# base-rate signals from the IPR-outcome literature: petitioner-side
# experience, owner-side experience, pair-specific history, art-area
# base rates, and per-patent multi-petition campaign indicators.
PRIOR_GROUP_COLUMNS: tuple[tuple[str, ...], ...] = (
    ("petitioner_real_party",),
    ("owner_real_party",),
    ("petitioner_real_party", "owner_real_party"),
    ("technology_center",),
    ("patent_number",),
    ("ptab_era",),
)
PRIOR_DATE_COLUMN: str = "petition_filing_date"

# Per `prediction_scope.md` §4: priors must aggregate only over training
# trials whose label was *observable* before this row's T₀ — i.e. their
# terminating-FWD (or non-FWD termination) date strictly precedes T₀.
# `label_resolution_date` is computed in `run_train.py` as
# `decision_issue_date.combine_first(termination_date)` and indexes the
# rolling cumsum inside `PriorEncoder` instead of `petition_filing_date`.
PRIOR_RESOLUTION_DATE_COLUMN: str = "label_resolution_date"


__all__ = [
    "MODELS",
    "MODEL_GRIDS",
    "MISSING_CATEGORY_SENTINEL",
    "PRIOR_GROUP_COLUMNS",
    "PRIOR_DATE_COLUMN",
    "PRIOR_RESOLUTION_DATE_COLUMN",
]
