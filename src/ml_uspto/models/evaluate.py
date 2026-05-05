"""Model evaluation and reporting."""

import logging
import os
import warnings

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    roc_auc_score,
)
from sklearn.model_selection import cross_validate
from sklearn.pipeline import Pipeline

from ml_uspto.clients.storage import LocalStorage, S3Storage
from ml_uspto.schemas.models import CalibrationBin, ModelMetrics

logger = logging.getLogger(__name__)

# Cosmetic noise from sklearn 1.8's internal joblib interaction; harmless.
# PYTHONWARNINGS is inherited by joblib's loky workers spawned under n_jobs=-1,
# which is where the warning actually originates.
warnings.filterwarnings("ignore", message=r".*sklearn\.utils\.parallel\.delayed.*")
os.environ.setdefault("PYTHONWARNINGS", "ignore::UserWarning:sklearn.utils.parallel")


DEFAULT_TIME_SERIES_SCORING: tuple[str, ...] = (
    "roc_auc",
    "average_precision",
    "f1",
)


def _date_safe_folds(
    sorted_dates: np.ndarray, n_splits: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Build forward-walking train/test fold indices that snap to date transitions.

    Mirrors sklearn's `TimeSeriesSplit` chunk layout (n_splits+1 equal
    chunks; fold k uses chunks 0..k as train, chunk k+1 as test) but
    pushes each cut *forward* to the first row whose date is strictly
    greater than the previous row's date. Same-day rows therefore never
    straddle a fold boundary — which matters here because joinder cases
    and multi-petition campaigns are filed in same-day batches and
    correlate strongly within a day.
    """
    n = len(sorted_dates)
    chunk = n // (n_splits + 1)

    def snap(idx: int) -> int:
        if idx <= 0:
            return 0
        if idx >= n:
            return n
        return int(np.searchsorted(sorted_dates, sorted_dates[idx - 1], side="right"))

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(1, n_splits + 1):
        train_end = snap(k * chunk)
        test_end = snap((k + 1) * chunk) if k < n_splits else n
        if train_end >= test_end:
            continue
        folds.append((np.arange(0, train_end), np.arange(train_end, test_end)))
    return folds


def time_series_cv(
    pipeline: Pipeline,
    X: pd.DataFrame,
    y: pd.Series,
    petition_dates: pd.Series,
    n_splits: int = 5,
    scoring: tuple[str, ...] | list[str] = DEFAULT_TIME_SERIES_SCORING,
) -> dict[str, np.ndarray]:
    """Forward-walking time-series CV against `petition_filing_date`.

    Sorts `(X, y)` by `petition_dates` and walks `n_splits` forward
    folds. Fold boundaries snap to date transitions via
    `_date_safe_folds` so same-day groups (joinder cases, multi-IPR
    campaigns) never straddle the train/test cut. Approximates
    deployment-time generalization: outcomes don't crystallize until
    ~18 months after T₀, so a strict T₀-frozen evaluation would also
    need to drop rows whose label was unknown as of the fold cutoff —
    skipped for the baseline.

    Args:
        pipeline: Estimator pipeline (preprocessor + model).
        X: Feature frame; must align with `y` and `petition_dates`.
        y: Binary labels.
        petition_dates: Petition filing dates (T₀) — same index as X.
        n_splits: Number of forward-walking folds.
        scoring: sklearn scoring keys to evaluate per fold.

    Returns:
        The raw `cross_validate` result dict (keys
        `test_<metric>`, `fit_time`, `score_time`).
    """
    order = np.argsort(pd.to_datetime(petition_dates).to_numpy(), kind="stable")
    X_sorted = X.iloc[order].reset_index(drop=True)
    y_sorted = y.iloc[order].reset_index(drop=True)
    sorted_dates = pd.to_datetime(petition_dates).to_numpy()[order]
    return cross_validate(
        pipeline,
        X_sorted,
        y_sorted,
        cv=_date_safe_folds(sorted_dates, n_splits),
        scoring=list(scoring),
        n_jobs=-1,
    )


def time_split(
    X: pd.DataFrame,
    y: pd.Series,
    petition_dates: pd.Series,
    *,
    holdout_after: str | pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Split (X, y, petition_dates) into train (< cutoff) and held-out test (≥ cutoff).

    The held-out tail is the deployment-honest evaluation slice — never
    inspected during model selection, hyperparameter search, or feature
    ablation. After everything is locked, `evaluate_model` runs once on
    the tail and that's the number you report.

    Args:
        X: Feature frame.
        y: Labels aligned with X.
        petition_dates: T₀ dates aligned with X.
        holdout_after: Cutoff (str like "2024-06-01" or `Timestamp`).
            Rows with `petition_filing_date >= cutoff` go to the held-out tail.

    Returns:
        `(X_train, X_test, y_train, y_test, dates_train, dates_test)`.
    """
    cutoff = pd.Timestamp(holdout_after)
    dates = pd.to_datetime(petition_dates)
    train_mask = dates < cutoff
    test_mask = ~train_mask
    return (
        X.loc[train_mask],
        X.loc[test_mask],
        y.loc[train_mask],
        y.loc[test_mask],
        dates.loc[train_mask],
        dates.loc[test_mask],
    )


def _calibration_bins(
    y_true: pd.Series, y_prob: np.ndarray, n_bins: int = 10
) -> list[CalibrationBin]:
    """Reliability-curve rows on uniform [0,1] bins, empty bins skipped.

    Per-bin counts are sklearn's `calibration_curve` output plus a
    histogram, since we want `n_samples` to weight reliability points
    when plotting (a bin holding 5 trials says little about miscalibration).
    """
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(y_prob, edges) - 1, 0, n_bins - 1)
    y_arr = np.asarray(y_true)
    bins: list[CalibrationBin] = []
    for b in range(n_bins):
        mask = idx == b
        n = int(mask.sum())
        if n == 0:
            continue
        bins.append(
            CalibrationBin(
                bin_lower=float(edges[b]),
                bin_upper=float(edges[b + 1]),
                mean_predicted=float(y_prob[mask].mean()),
                fraction_positive=float(y_arr[mask].mean()),
                n_samples=n,
            )
        )
    return bins


def evaluate_model(
    model: BaseEstimator, X_test: pd.DataFrame, y_test: pd.Series, model_name: str
) -> ModelMetrics:
    """Compute metrics on the test set."""
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]

    metrics = ModelMetrics(
        model_name=model_name,
        accuracy=accuracy_score(y_test, y_pred),
        roc_auc=roc_auc_score(y_test, y_prob),
        classification_report=classification_report(y_test, y_pred, output_dict=True),
        calibration_bins=_calibration_bins(y_test, y_prob),
    )

    logger.info(
        "%s — Accuracy: %.4f, AUC: %.4f",
        metrics.model_name,
        metrics.accuracy,
        metrics.roc_auc,
    )
    return metrics


def save_metrics(
    all_metrics: list[ModelMetrics],
    storage: LocalStorage | S3Storage,
    *,
    key: str,
) -> None:
    """Persist `all_metrics` under the `metrics` object bucket as `<key>.json`.

    Routed through the storage backend (Hard Rule #5) so the same call
    works for local-FS dev runs and S3-backed Fargate runs without the
    caller knowing which.
    """
    payload = {"models": [m.model_dump() for m in all_metrics]}
    storage.save_object("metrics", key, payload)
    logger.info("Metrics saved to metrics/%s.json", key)
