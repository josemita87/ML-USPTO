"""Step 3: Feature engineering + model training with cross-validation."""

import logging
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.engineering import build_features
from src.models.train import MODELS, split_data, train_and_evaluate_cv
from src.settings import get_settings
from src.utils.io import load_parquet, resolve_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    settings = get_settings()
    settings.ensure_dirs()

    df = load_parquet(resolve_path(settings.data.processed_dir / "ipr_clean.parquet"))
    logger.info("Loaded %d records", len(df))

    X = build_features(df)
    y = df["instituted"]

    X_train, X_test, y_train, y_test = split_data(
        X, y,
        test_size=settings.model.test_size,
        random_state=settings.model.random_state,
    )
    logger.info("Train: %d, Test: %d", len(X_train), len(X_test))

    models_dir = resolve_path(settings.data.models_dir)
    X_test.to_parquet(models_dir / "X_test.parquet")
    y_test.to_frame().to_parquet(models_dir / "y_test.parquet")

    for model_name in MODELS:
        model, cv_scores = train_and_evaluate_cv(
            X_train, y_train, model_name=model_name, cv_folds=settings.model.cv_folds
        )
        model_path = models_dir / f"{model_name}.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)
        logger.info("Saved %s to %s", model_name, model_path)


if __name__ == "__main__":
    main()
