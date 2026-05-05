"""Plotting helpers for trained model artifacts (ROC, confusion, importances)."""

import logging
from pathlib import Path
from typing import Literal

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.base import BaseEstimator
from sklearn.metrics import confusion_matrix, roc_auc_score, roc_curve

from ml_uspto.schemas.models import GridSearchResult

logger = logging.getLogger(__name__)


def plot_roc_curves(
    models: dict[str, BaseEstimator],
    X_test: pd.DataFrame,
    y_test: pd.Series,
    output_path: Path,
) -> None:
    """Plot ROC curves for all models."""
    plt.figure(figsize=(8, 6))

    for name, model in models.items():
        y_prob = model.predict_proba(X_test)[:, 1]
        fpr, tpr, _ = roc_curve(y_test, y_prob)
        auc = roc_auc_score(y_test, y_prob)
        plt.plot(fpr, tpr, label=f"{name} (AUC={auc:.3f})")

    plt.plot([0, 1], [0, 1], "k--", alpha=0.3)
    plt.xlabel("False Positive Rate")
    plt.ylabel("True Positive Rate")
    plt.title("ROC Curves — IPR Trial Outcome Prediction")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info("ROC curve saved to %s", output_path)


def plot_confusion_matrix(
    model: BaseEstimator,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    model_name: str,
    output_path: Path,
) -> None:
    """Render and save the confusion matrix for `model` on the test set."""
    y_pred = model.predict(X_test)
    cm = confusion_matrix(y_test, y_pred)

    labels = ["Not Cancelled", "All Cancelled"]
    plt.figure(figsize=(6, 5))
    sns.heatmap(
        cm,
        annot=True,
        fmt="d",
        cmap="Blues",
        xticklabels=labels,
        yticklabels=labels,
    )
    plt.xlabel("Predicted")
    plt.ylabel("Actual")
    plt.title(f"Confusion Matrix — {model_name}")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_feature_importance(
    model: BaseEstimator,
    feature_names: list[str],
    model_name: str,
    output_path: Path,
    top_n: int = 15,
) -> None:
    """Plot top feature importances (works for tree-based models)."""
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
    elif hasattr(model, "coef_"):
        importances = np.abs(model.coef_[0])
    else:
        logger.warning("Model %s has no feature importances", model_name)
        return

    idx = np.argsort(importances)[-top_n:]
    plt.figure(figsize=(8, 6))
    plt.barh(range(len(idx)), importances[idx])
    plt.yticks(range(len(idx)), [feature_names[i] for i in idx])
    plt.xlabel("Importance")
    plt.title(f"Top {top_n} Features — {model_name}")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info("Feature importance plot saved to %s", output_path)


def plot_grid_search_folds(
    result: GridSearchResult,
    output_path: Path,
    mode: Literal["best", "all"] = "best",
) -> None:
    """Plot per-fold ROC-AUC across the date-safe forward-walking CV folds.

    `mode="best"` draws a single line for `result.best_params`.
    `mode="all"` overlays every combo as a faint grey line and the
    winner as a bold colored line — surfaces whether the winner is an
    outlier riding one lucky fold or consistently strong across time.
    """
    fold_idx = np.arange(1, result.n_splits + 1)
    plt.figure(figsize=(8, 5))

    if mode == "all":
        for entry in result.entries:
            if entry.params == result.best_params:
                continue
            plt.plot(
                fold_idx[: len(entry.fold_roc_auc)],
                entry.fold_roc_auc,
                color="grey",
                alpha=0.25,
                linewidth=0.8,
            )

    best = next(e for e in result.entries if e.params == result.best_params)
    plt.plot(
        fold_idx[: len(best.fold_roc_auc)],
        best.fold_roc_auc,
        color="C0",
        linewidth=2.0,
        marker="o",
        label=f"best (mean={best.mean_roc_auc:.3f})",
    )

    plt.xticks(fold_idx)
    plt.xlabel("Fold (chronological)")
    plt.ylabel("ROC-AUC")
    plt.title(f"CV ROC-AUC by fold — {result.model_name}")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info("Grid-search fold plot saved to %s", output_path)
