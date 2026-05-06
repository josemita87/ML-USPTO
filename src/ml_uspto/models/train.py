"""Model training pipeline."""

import logging
from typing import Any

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from ml_uspto.models.evaluate import time_series_cv
from ml_uspto.models.preprocessing import build_pipeline
from ml_uspto.models.schemas.constants import MODELS
from ml_uspto.models.schemas.enums import ModelName

logger = logging.getLogger(__name__)


def train_and_evaluate_cv(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    petition_dates_train: pd.Series,
    model_name: ModelName = ModelName.RANDOM_FOREST,
    cv_folds: int = 5,
) -> tuple[Pipeline, dict[str, Any]]:
    """Time-series CV on the training split and refit on the full train.

    Calls `time_series_cv` for date-aware forward-walking folds (so
    same-day groups don't straddle the train/test cut), then refits the
    pipeline on `(X_train, y_train)` for held-out evaluation by the
    caller. Wraps `MODELS[model_name]()` in `build_pipeline`, whose
    fold-tier preprocessor (OHE + median imputer) is refit per fold by
    sklearn `cross_validate` — OHE column sets and imputation medians
    are derived from training rows only. The corpus-tier rolling
    encodings (`<group>_prior_rate`, `<group>_prior_count`,
    `<col>_frequency`) are already on the frame; `attach_rolling_encodings`
    upstream fits them once over the full corpus under a strict-`<` T₀
    gate, so they don't refit per fold.

    The held-out tail (typically last 12–18 months) is split off
    upstream by `evaluate.time_split`; this function only sees rows
    earlier than the cutoff. The CV metrics returned here drive model
    *selection*; the deployment-honest number comes from running
    `evaluate.evaluate_model` on the held-out tail once after
    everything is locked.

    Args:
        X_train: Feature matrix on the training split.
        y_train: Binary `cancelled` labels aligned with `X_train`.
        petition_dates_train: T₀ dates aligned with `X_train`; passed
            through to `time_series_cv` so folds respect chronology.
        model_name: Estimator key into `MODELS`.
        cv_folds: Forward-walking fold count.

    Returns:
        `(refit_pipeline, cv_results_dict)` where `cv_results_dict`
        is the raw `cross_validate` result (keys `test_<metric>`,
        `fit_time`, `score_time`).
    """
    pipeline = build_pipeline(MODELS[model_name]())
    logger.info("Time-series CV on %s with %d folds", model_name.value, cv_folds)

    cv_results = time_series_cv(
        pipeline, X_train, y_train, petition_dates_train, n_splits=cv_folds
    )
    auc = cv_results.get("test_roc_auc", np.array([]))
    if auc.size:
        logger.info(
            "%s CV ROC-AUC: %.4f (+/- %.4f)",
            model_name.value,
            float(auc.mean()),
            float(auc.std()),
        )

    pipeline.fit(X_train, y_train)
    return pipeline, cv_results
