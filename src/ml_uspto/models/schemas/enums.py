"""Enums for the training subpackage."""

from enum import StrEnum


class ModelName(StrEnum):
    """Identifiers for trainable estimators registered in `models.schemas.constants.MODELS`."""

    RANDOM_FOREST = "random_forest"
