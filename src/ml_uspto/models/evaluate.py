"""Model evaluation and reporting."""

import json
import logging
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    roc_auc_score,
    roc_curve,
)

from ml_uspto.schemas.models import ModelMetrics

logger = logging.getLogger(__name__)


def evaluate_model(
    model, X_test: pd.DataFrame, y_test: pd.Series, model_name: str
) -> ModelMetrics:
    """Compute metrics on the test set."""
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]

    metrics = ModelMetrics(
        model_name=model_name,
        accuracy=accuracy_score(y_test, y_pred),
        roc_auc=roc_auc_score(y_test, y_prob),
        classification_report=classification_report(y_test, y_pred, output_dict=True),
    )

    logger.info(
        "%s — Accuracy: %.4f, AUC: %.4f",
        metrics.model_name,
        metrics.accuracy,
        metrics.roc_auc,
    )
    return metrics


def plot_roc_curves(
    models: dict, X_test: pd.DataFrame, y_test: pd.Series, output_path: Path
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
    plt.title("ROC Curves — IPR Institution Prediction")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info("ROC curve saved to %s", output_path)


def plot_confusion_matrix(
    model, X_test: pd.DataFrame, y_test: pd.Series, model_name: str, output_path: Path
) -> None:
    y_pred = model.predict(X_test)
    cm = confusion_matrix(y_test, y_pred)

    labels = ["Denied", "Instituted"]
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
    model, feature_names: list[str], model_name: str, output_path: Path, top_n: int = 15
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


def save_metrics(all_metrics: list[ModelMetrics], output_path: Path) -> None:
    payload = [m.model_dump() for m in all_metrics]
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info("Metrics saved to %s", output_path)
