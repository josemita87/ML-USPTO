"""Model training pipeline."""

import logging

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score, train_test_split
from xgboost import XGBClassifier

logger = logging.getLogger(__name__)

MODELS = {
    "logistic_regression": lambda: LogisticRegression(max_iter=1000, random_state=42),
    "random_forest": lambda: RandomForestClassifier(
        n_estimators=200, max_depth=10, random_state=42, n_jobs=-1
    ),
    "xgboost": lambda: XGBClassifier(
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
    return train_test_split(X, y, test_size=test_size, random_state=random_state, stratify=y)


def train_and_evaluate_cv(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    model_name: str = "xgboost",
    cv_folds: int = 5,
) -> tuple:
    """Train a model with cross-validation and return (model, cv_scores)."""
    if model_name not in MODELS:
        raise ValueError(f"Unknown model: {model_name}. Choose from {list(MODELS)}")

    model = MODELS[model_name]()
    logger.info("Cross-validating %s with %d folds", model_name, cv_folds)

    scores = cross_val_score(model, X_train, y_train, cv=cv_folds, scoring="roc_auc")
    logger.info("%s CV AUC: %.4f (+/- %.4f)", model_name, scores.mean(), scores.std())

    # Fit on full training set
    model.fit(X_train, y_train)
    return model, scores
