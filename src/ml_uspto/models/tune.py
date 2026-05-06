"""Grid search over `MODEL_GRIDS` using date-safe forward-walking CV."""

import itertools
import logging
from typing import Any

import numpy as np
import pandas as pd

from ml_uspto.models.evaluate import time_series_cv
from ml_uspto.models.preprocessing import build_pipeline
from ml_uspto.models.schemas.constants import MODEL_GRIDS, MODELS
from ml_uspto.models.schemas.enums import ModelName
from ml_uspto.schemas.models import GridSearchEntry, GridSearchResult

logger = logging.getLogger(__name__)


def _iter_param_combos(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of a `{param: [values]}` grid into a list of combos."""
    if not grid:
        return [{}]
    keys = list(grid.keys())
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*grid.values())]


def grid_search_cv(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    petition_dates_train: pd.Series,
    model_name: ModelName,
    grid: dict[str, list[Any]] | None = None,
    cv_folds: int = 5,
) -> GridSearchResult:
    """Exhaustive grid search ranked by mean ROC-AUC across date-safe folds.

    For each parameter combo in `grid` (defaulting to
    `MODEL_GRIDS[model_name]`), instantiates the estimator from
    `MODELS[model_name]()`, applies `set_params(**combo)`, wraps it with
    `build_pipeline` (whose fold-tier OHE + median imputer refit per
    fold; corpus-tier rolling encodings are pre-attached upstream and
    leakage-safe by row-local T₀ gating), and evaluates via
    `evaluate.time_series_cv`. Cannot use sklearn's `GridSearchCV`
    because it would shuffle folds and break the forward-walking
    date-safe split this codebase requires.

    Selection metric is ROC-AUC mean across folds. Returns every combo's
    scores so the choice is auditable; `best_params` is the argmax row.

    Args:
        X_train: Training feature matrix (already through `time_split`,
            so rows precede the held-out cutoff).
        y_train: Binary `cancelled` labels aligned with `X_train`.
        petition_dates_train: T₀ dates aligned with `X_train`; passed to
            `time_series_cv` for chronological fold construction.
        model_name: Estimator key into `MODELS` and `MODEL_GRIDS`.
        grid: Override grid; defaults to `MODEL_GRIDS[model_name]`.
        cv_folds: Forward-walking fold count.

    Returns:
        `GridSearchResult` containing every combo's per-fold scores plus
        the winning params.
    """
    if grid is None:
        grid = MODEL_GRIDS[model_name]
    combos = _iter_param_combos(grid)
    logger.info(
        "Grid search on %s — %d combos × %d folds",
        model_name.value,
        len(combos),
        cv_folds,
    )

    entries: list[GridSearchEntry] = []
    for i, params in enumerate(combos, start=1):
        estimator = MODELS[model_name]()
        estimator.set_params(**params)
        pipeline = build_pipeline(estimator)
        cv = time_series_cv(
            pipeline, X_train, y_train, petition_dates_train, n_splits=cv_folds
        )
        fold_auc = cv.get("test_roc_auc", np.array([]))
        mean_auc = float(fold_auc.mean()) if fold_auc.size else float("nan")
        std_auc = float(fold_auc.std()) if fold_auc.size else float("nan")
        fit_time = cv.get("fit_time", np.array([]))
        entries.append(
            GridSearchEntry(
                model_name=model_name.value,
                params=params,
                mean_roc_auc=mean_auc,
                std_roc_auc=std_auc,
                fold_roc_auc=[float(s) for s in fold_auc.tolist()],
                mean_fit_time=float(fit_time.mean()) if fit_time.size else 0.0,
            )
        )
        logger.info(
            "[%d/%d] %s — AUC %.4f (+/- %.4f)",
            i,
            len(combos),
            params,
            mean_auc,
            std_auc,
        )

    best = max(entries, key=lambda e: e.mean_roc_auc)
    logger.info(
        "Best %s combo: %s — AUC %.4f",
        model_name.value,
        best.params,
        best.mean_roc_auc,
    )
    return GridSearchResult(
        model_name=model_name.value,
        n_splits=cv_folds,
        entries=entries,
        best_params=best.params,
        best_mean_roc_auc=best.mean_roc_auc,
    )


__all__ = ["grid_search_cv"]
