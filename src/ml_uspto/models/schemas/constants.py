"""Constants for `ml_uspto.models`."""

from collections.abc import Callable
from typing import Any

from sklearn.ensemble import RandomForestClassifier

from ml_uspto.models.schemas.enums import ModelName

MODELS: dict[ModelName, Callable[[], Any]] = {
    ModelName.RANDOM_FOREST: lambda: RandomForestClassifier(
        n_estimators=200, max_depth=10, random_state=42, n_jobs=-1
    ),
}


__all__ = ["MODELS"]
