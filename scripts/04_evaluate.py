"""Step 4: Evaluate all trained models and generate reports."""

import logging
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.models.evaluate import (
    evaluate_model,
    plot_confusion_matrix,
    plot_feature_importance,
    plot_roc_curves,
    save_metrics,
)
from src.models.train import MODELS
from src.settings import get_settings
from src.utils.io import resolve_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    settings = get_settings()
    models_dir = resolve_path(settings.data.models_dir)

    X_test = pd.read_parquet(models_dir / "X_test.parquet")
    y_test = pd.read_parquet(models_dir / "y_test.parquet")["instituted"]

    trained_models = {}
    all_metrics = []

    for model_name in MODELS:
        model_path = models_dir / f"{model_name}.pkl"
        if not model_path.exists():
            logger.warning("Model %s not found, skipping", model_name)
            continue

        with open(model_path, "rb") as f:
            model = pickle.load(f)

        trained_models[model_name] = model
        metrics = evaluate_model(model, X_test, y_test, model_name)
        all_metrics.append(metrics)

        plot_confusion_matrix(
            model, X_test, y_test, model_name,
            models_dir / f"{model_name}_confusion.png",
        )
        plot_feature_importance(
            model, list(X_test.columns), model_name,
            models_dir / f"{model_name}_features.png",
        )

    if trained_models:
        plot_roc_curves(trained_models, X_test, y_test, models_dir / "roc_curves.png")

    save_metrics(all_metrics, models_dir / "metrics.json")
    logger.info("Evaluation complete.")


if __name__ == "__main__":
    main()
