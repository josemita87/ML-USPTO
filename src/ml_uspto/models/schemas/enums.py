"""Enums for the training subpackage."""

from enum import StrEnum


class ModelName(StrEnum):
    """Identifiers for trainable estimators registered in `models.train.MODELS`."""

    RANDOM_FOREST = "random_forest"
