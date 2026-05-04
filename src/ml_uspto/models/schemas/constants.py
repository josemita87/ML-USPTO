"""Constants for `ml_uspto.models`."""

from collections.abc import Callable
from typing import Any

from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier

from ml_uspto.models.schemas.enums import ModelName

MODELS: dict[ModelName, Callable[[], Any]] = {
    # Sweep on the enriched feature matrix (entity_size, inventor_geo,
    # petition_text_length, n_cpc_codes, n_cpc_subclasses) picked these
    # knobs as the held-out winner (HO AUC 0.6234 on the 2023+ tail,
    # mature_days=540). Notable: `max_depth=10` cap underfits badly
    # (HO 0.575); removing it is the single biggest lever. `max_features=0.3`
    # lets each split see ~25 of the ~85 post-preprocessing columns —
    # `sqrt` is too restrictive for this signal-poor matrix where many
    # weak features need to compose. `min_samples_leaf=5` damps the
    # variance-floor RF would otherwise hit on rare counsel/party priors.
    ModelName.RANDOM_FOREST: lambda: RandomForestClassifier(
        n_estimators=500,
        max_depth=None,
        max_features=0.3,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    ),
    # Sklearn's LightGBM-equivalent. On the current cohort it picks up
    # ~1.4 ROC-AUC points over RF on the held-out tail, which matches the
    # univariate diagnostic (no feature exceeds AUC 0.55, so the win comes
    # from additive accumulation across many weak signals — exactly what
    # gradient boosting is for and where bagged trees plateau). Defaults
    # beat hand-tuned variants because the dataset has too little signal
    # to support stronger regularization without underfitting.
    ModelName.HIST_GRADIENT_BOOSTING: lambda: HistGradientBoostingClassifier(
        random_state=42,
    ),
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


__all__ = [
    "MODELS",
    "MISSING_CATEGORY_SENTINEL",
    "PRIOR_GROUP_COLUMNS",
    "PRIOR_DATE_COLUMN",
]
