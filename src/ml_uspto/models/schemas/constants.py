"""Constants for `ml_uspto.models`."""

from collections.abc import Callable
from typing import Any

from sklearn.ensemble import RandomForestClassifier

from ml_uspto.models.schemas.enums import ModelName

MODELS: dict[ModelName, Callable[[], Any]] = {
    ModelName.RANDOM_FOREST: lambda: RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    ),
}

# Sentinel inserted by `preprocessing.build_preprocessor`'s OHE branch
# in place of NaN for categorical columns, so missingness becomes an
# explicit one-hot level rather than collapsing into all-zeros.
MISSING_CATEGORY_SENTINEL: str = "__missing__"


__all__ = ["MODELS", "MISSING_CATEGORY_SENTINEL"]
