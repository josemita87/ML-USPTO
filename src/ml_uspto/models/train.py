"""Model training pipeline."""

import logging
from typing import Any

import pandas as pd
from sklearn.model_selection import cross_val_score
from sklearn.pipeline import Pipeline

from ml_uspto.models.preprocessing import build_pipeline
from ml_uspto.models.schemas.constants import MODELS
from ml_uspto.models.schemas.enums import ModelName

logger = logging.getLogger(__name__)


def train_and_evaluate_cv(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    model_name: ModelName = ModelName.RANDOM_FOREST,
    cv_folds: int = 5,
) -> tuple[Pipeline, Any]:
    """Cross-validate a preprocessor+estimator pipeline and refit on the full train split.

    Wraps `MODELS[model_name]()` in `build_pipeline` so the
    preprocessor (frequency encoder, one-hot encoder, median imputer)
    is refit on the training rows of each CV fold — preventing the
    schema/value leakage a corpus-wide encoding would introduce.

    Args:
        X_train: Feature matrix from `features.transforms.build_features`.
        y_train: Binary `cancelled` labels.
        model_name: Estimator key into `MODELS`.
        cv_folds: Number of CV folds for `cross_val_score`.

    Returns:
        Tuple of (refit pipeline, ROC-AUC scores per fold).
    """
    pipeline = build_pipeline(MODELS[model_name]())
    logger.info("Cross-validating %s with %d folds", model_name.value, cv_folds)

    scores = cross_val_score(pipeline, X_train, y_train, cv=cv_folds, scoring="roc_auc")
    logger.info("%s CV AUC: %.4f (+/- %.4f)", model_name.value, scores.mean(), scores.std())

    pipeline.fit(X_train, y_train)
    return pipeline, scores
