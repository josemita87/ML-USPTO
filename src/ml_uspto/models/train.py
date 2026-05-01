"""Model training pipeline."""

import logging
from collections.abc import Callable
from typing import Any

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score, train_test_split
from xgboost import XGBClassifier

from ml_uspto.models.schemas.enums import ModelName

logger = logging.getLogger(__name__)

MODELS: dict[ModelName, Callable[[], Any]] = {
    ModelName.LOGISTIC_REGRESSION: lambda: LogisticRegression(max_iter=1000, random_state=42),
    ModelName.RANDOM_FOREST: lambda: RandomForestClassifier(
        n_estimators=200, max_depth=10, random_state=42, n_jobs=-1
    ),
    ModelName.XGBOOST: lambda: XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        random_state=42,
        use_label_encoder=False,
        eval_metric="logloss",
    ),
}


def split_data(
    X: pd.DataFrame, y: pd.Series, test_size: float = 0.2, random_state: int = 42
) -> tuple:
    """Stratified train/test split — wraps `sklearn.train_test_split` with `stratify=y`."""
    return train_test_split(X, y, test_size=test_size, random_state=random_state, stratify=y)


def train_and_evaluate_cv(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    model_name: ModelName = ModelName.XGBOOST,
    cv_folds: int = 5,
) -> tuple:
    """Train a model with cross-validation and return (model, cv_scores)."""
    model = MODELS[model_name]()
    logger.info("Cross-validating %s with %d folds", model_name.value, cv_folds)

    scores = cross_val_score(model, X_train, y_train, cv=cv_folds, scoring="roc_auc")
    logger.info("%s CV AUC: %.4f (+/- %.4f)", model_name.value, scores.mean(), scores.std())

    model.fit(X_train, y_train)
    return model, scores
