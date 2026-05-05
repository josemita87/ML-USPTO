"""Stage 6 driver — model-ready feature matrix → trained model + metrics.

Reads `Frame.FEATURES` (model-ready feature matrix, `trial_number`-keyed)
and `Frame.JOINED_TRIALS` (for the `cancelled` label and
`petition_filing_date` T₀ column), runs forward-walking time-series CV on
the pre-cutoff slice via `models.train.train_and_evaluate_cv`, refits on
the full training slice, evaluates once on the held-out tail with
`models.evaluate.evaluate_model`, and persists `ModelMetrics` under the
`metrics` object bucket.

Pure pandas + sklearn — no HTTP, no raw-cache reads. Run after
`drivers/run_features.py`.
"""

import argparse
import logging

import pandas as pd

from ml_uspto.clients.storage import get_storage
from ml_uspto.models.evaluate import evaluate_model, save_metrics, time_split
from ml_uspto.models.schemas.enums import ModelName
from ml_uspto.models.train import train_and_evaluate_cv
from ml_uspto.schemas.enums import Frame
from ml_uspto.settings import get_settings


def _parse_args() -> argparse.Namespace:
    """CLI knobs: model choice, held-out cutoff, CV fold count."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model",
        type=ModelName,
        choices=list(ModelName),
        default=ModelName.RANDOM_FOREST,
        help="Estimator key into MODELS (default: random_forest).",
    )
    parser.add_argument(
        "--holdout-after",
        default=str(get_settings().model.holdout_after),
        help=(
            "Cutoff date (YYYY-MM-DD). Rows with petition_filing_date >= cutoff "
            "form the deployment-honest held-out tail. Default from settings.yaml "
            "(model.holdout_after) — frozen for reproducibility."
        ),
    )
    parser.add_argument(
        "--cv-folds",
        type=int,
        default=get_settings().model.cv_folds,
        help="Forward-walking CV fold count (default from settings.yaml).",
    )
    parser.add_argument(
        "--mature-days",
        type=int,
        default=get_settings().model.mature_days,
        help=(
            "Drop rows whose label hasn't had time to crystallize: "
            "`today - petition_filing_date < mature_days` are excluded "
            "from BOTH train and held-out. Default from settings.yaml "
            "(model.mature_days). Set to 0 to disable."
        ),
    )
    parser.add_argument(
        "--today",
        default=str(get_settings().model.experiment_today),
        help=(
            "Pinned 'today' for the mature-days computation (YYYY-MM-DD). "
            "Default from settings.yaml (model.experiment_today) — frozen "
            "so reruns are reproducible."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Train + CV-evaluate one estimator and persist held-out metrics."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args()

    storage = get_storage()
    features = storage.load_frame(Frame.FEATURES)
    joined = storage.load_frame(Frame.JOINED_TRIALS)

    # Label and T₀ live on the joined frame; features.transforms.build_features
    # deliberately omits both so the feature pipeline stays purely row-local.
    # `patent_number` is also pulled in here — it's used solely as the
    # group key for the per-patent prior in `PriorEncoder` and is dropped
    # by `remainder="drop"` from every other branch.
    #
    # `label_resolution_date` is the date the trial's label crystallized:
    # FWD-issue date for FWD-resolved trials, otherwise the termination
    # date. `PriorEncoder` orders its rolling-rate cumsum by *this* date
    # — not by petition_filing_date — so a training trial contributes to
    # a row's prior only after its label was actually observable, per
    # `prediction_scope.md` §4.
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
    ).combine_first(
        pd.to_datetime(label_cols["termination_date"], errors="coerce")
    )
    label_cols = label_cols.drop(columns=["decision_issue_date", "termination_date"])
    features["trial_number"] = features["trial_number"].astype(str)
    merged = features.merge(label_cols, on="trial_number", how="inner")

    # Mature-label filter: rows whose petition is more recent than
    # `mature_days` ago can't have a fully-crystallized label —
    # FWDs take ~18 months, so anything under that horizon is
    # dominated by fast-resolution outcomes (settlements, institution
    # denials, discretionary denials) all labeled cancelled=0. Applied
    # to BOTH train and held-out so the populations stay comparable.
    if args.mature_days > 0:
        today = pd.Timestamp(args.today)
        mature_cut = today - pd.Timedelta(days=args.mature_days)
        n_before = len(merged)
        merged = merged[pd.to_datetime(merged["petition_filing_date"]) <= mature_cut].copy()
        print(
            f"mature filter: {n_before} -> {len(merged)} rows "
            f"(petition_filing_date <= {mature_cut.date()}, "
            f"≥{args.mature_days} days old as of {today.date()})"
        )

    y = merged["cancelled"].astype(int)
    petition_dates = merged["petition_filing_date"]
    # Keep `petition_filing_date` and `patent_number` in X so the
    # `PriorEncoder` branches can read them; every other branch's
    # column-selector ignores them, so `remainder="drop"` strips them
    # from the final feature matrix.
    X = merged.drop(columns=["trial_number", "cancelled"])

    # Held-out tail is the deployment-honest evaluation slice — never seen
    # by CV, hyperparameter search, or feature ablation.
    X_train, X_test, y_train, y_test, dates_train, _ = time_split(
        X, y, petition_dates, holdout_after=args.holdout_after
    )

    pipeline, cv_results = train_and_evaluate_cv(
        X_train,
        y_train,
        dates_train,
        model_name=args.model,
        cv_folds=args.cv_folds,
    )

    metrics = evaluate_model(pipeline, X_test, y_test, args.model.value)
    save_metrics([metrics], storage, key=args.model.value)

    cv_auc = cv_results.get("test_roc_auc")
    cv_auc_mean = float(cv_auc.mean()) if cv_auc is not None and cv_auc.size else float("nan")
    print(f"trained: model={args.model.value} holdout_after={args.holdout_after}")
    print(f"  train rows:           {len(X_train)}")
    print(f"  held-out tail rows:   {len(X_test)}")
    print(f"  CV ROC-AUC (mean):    {cv_auc_mean:.4f}")
    print(f"  held-out accuracy:    {metrics.accuracy:.4f}")
    print(f"  held-out ROC-AUC:     {metrics.roc_auc:.4f}")
    print(f"  metrics -> metrics/{args.model.value}.json")


if __name__ == "__main__":
    main()
