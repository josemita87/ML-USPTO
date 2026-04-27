"""Enums for the training subpackage.

Used to keep callers off of free-string Literals: callers pass
`ModelName.XGBOOST`, not `"xgboost"`.
"""

from enum import StrEnum


class ModelName(StrEnum):
    """Identifiers for trainable estimators registered in `models.train.MODELS`."""

    LOGISTIC_REGRESSION = "logistic_regression"
    RANDOM_FOREST = "random_forest"
    XGBOOST = "xgboost"
