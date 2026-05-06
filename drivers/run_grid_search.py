"""Grid search over `MODEL_GRIDS[model]` on the pre-cutoff slice.

Mirrors `drivers/run_train.py`'s data-loading + mature-days filter +
`time_split`, then calls `models.tune.grid_search_cv` instead of a
single fit. Persists the full `GridSearchResult` (every combo's
per-fold scores plus the winning params) under the `metrics` bucket as
`grid_search_<model>.json` so the choice is auditable later.

Held-out tail is loaded but never touched here — grid search runs on
the training slice only, per the deployment-honest evaluation contract
in `prediction_scope.md`.
"""

import argparse
import logging

import pandas as pd

from ml_uspto import paths
from ml_uspto.clients.storage import get_storage
from ml_uspto.models.evaluate import time_split
from ml_uspto.models.plots import plot_grid_search_folds
from ml_uspto.models.preprocessing import attach_rolling_encodings
from ml_uspto.models.schemas.enums import ModelName
from ml_uspto.models.tune import grid_search_cv
from ml_uspto.schemas.enums import Frame
from ml_uspto.settings import get_settings


def _parse_args() -> argparse.Namespace:
    """Same knobs as `run_train.py` minus the single-fit options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        type=ModelName,
        choices=list(ModelName),
        nargs="+",
        default=list(ModelName),
        help="One or more estimator keys (default: all models).",
    )
    parser.add_argument(
        "--holdout-after",
        default=str(get_settings().model.holdout_after),
        help="Cutoff (YYYY-MM-DD). Default from settings.yaml (model.holdout_after).",
    )
    parser.add_argument(
        "--cv-folds",
        type=int,
        default=get_settings().model.cv_folds,
        help="Forward-walking CV fold count (default from settings.yaml).",
    )
    parser.add_argument(
        "--mature-days", type=int, default=get_settings().model.mature_days
    )
    parser.add_argument(
        "--today", default=str(get_settings().model.experiment_today)
    )
    parser.add_argument(
        "--plot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Auto-generate per-fold AUC plot (mode=all) alongside the JSON. "
            "Disable with --no-plot."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Load → filter → split → grid_search_cv → persist result."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args()

    storage = get_storage()
    features = storage.load_frame(Frame.FEATURES)
    joined = storage.load_frame(Frame.JOINED_TRIALS)

    label_cols = joined[
        [
            "trial_number",
            "cancelled",
            "petition_filing_date",
            "patent_number",
            "decision_issue_date",
            "termination_date",
        ]
    ].copy()
    label_cols["trial_number"] = label_cols["trial_number"].astype(str)
    label_cols["patent_number"] = label_cols["patent_number"].astype(str)
    label_cols["label_resolution_date"] = pd.to_datetime(
        label_cols["decision_issue_date"], errors="coerce"
    ).combine_first(pd.to_datetime(label_cols["termination_date"], errors="coerce"))
    label_cols = label_cols.drop(columns=["decision_issue_date", "termination_date"])
    features["trial_number"] = features["trial_number"].astype(str)
    merged = features.merge(label_cols, on="trial_number", how="inner")

    if args.mature_days > 0:
        today = pd.Timestamp(args.today)
        mature_cut = today - pd.Timedelta(days=args.mature_days)
        n_before = len(merged)
        merged = merged[
            pd.to_datetime(merged["petition_filing_date"]) <= mature_cut
        ].copy()
        print(
            f"mature filter: {n_before} -> {len(merged)} rows "
            f"(petition_filing_date <= {mature_cut.date()})"
        )

    y = merged["cancelled"].astype(int)
    merged = attach_rolling_encodings(merged, y)
    petition_dates = merged["petition_filing_date"]
    X = merged.drop(columns=["trial_number", "cancelled"])

    X_train, _, y_train, _, dates_train, _ = time_split(
        X, y, petition_dates, holdout_after=args.holdout_after
    )

    for model_name in args.model:
        result = grid_search_cv(
            X_train,
            y_train,
            dates_train,
            model_name=model_name,
            cv_folds=args.cv_folds,
        )

        key = f"grid_search_{model_name.value}"
        storage.save_object("metrics", key, result.model_dump())

        print(f"grid search: model={model_name.value} combos={len(result.entries)}")
        print(f"  best params:    {result.best_params}")
        print(f"  best mean AUC:  {result.best_mean_roc_auc:.4f}")
        print(f"  result -> metrics/{key}.json")

        if args.plot:
            plot_path = paths.raw_dir() / "metrics" / f"{key}_folds.png"
            plot_path.parent.mkdir(parents=True, exist_ok=True)
            plot_grid_search_folds(result, plot_path, mode="all")
            print(f"  plot   -> metrics/{key}_folds.png")


if __name__ == "__main__":
    main()
