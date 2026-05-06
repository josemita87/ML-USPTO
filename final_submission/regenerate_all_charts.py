"""Regenerate all numbered figures (01–09) from the latest training run.

Mirrors `run_training_for_report.py`: refits the canonical pipeline on the
pre-cutoff slice, evaluates on the held-out tail, then renders every
chart that lives in `final_submission/figures/`. Reads
`predictions_for_report.csv` / `metrics_for_report.json` for the values
that are already persisted; refits because feature importances aren't.

`report_*` charts (Figure 1 in `report.pdf`) are owned by
`build_report_figures.py`; charts 10–12 + slide7_* are owned by
`figures/build_slide7_charts.py`. This script does not touch those.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (
    auc,
    confusion_matrix,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_curve,
)

from ml_uspto.clients.storage import get_storage
from ml_uspto.models.evaluate import time_split
from ml_uspto.models.preprocessing import attach_rolling_encodings, build_pipeline
from ml_uspto.models.schemas.constants import MODELS
from ml_uspto.models.schemas.enums import ModelName
from ml_uspto.schemas.enums import Frame
from ml_uspto.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
PRED_PATH = ROOT / "final_submission" / "predictions_for_report.csv"
METRICS_PATH = ROOT / "final_submission" / "metrics_for_report.json"
FIG_DIR = ROOT / "final_submission" / "figures"

# Single source of truth for these knobs — `config/settings.yaml::model`.
# Don't shadow with module-level constants; that's how the driver and the
# notebook drift apart between report rebuilds.
_MODEL = get_settings().model
MATURE_DAYS = _MODEL.mature_days
HOLDOUT_AFTER = _MODEL.holdout_after.isoformat()
TODAY = pd.Timestamp(_MODEL.experiment_today)


def _load_split_and_fit():
    storage = get_storage()
    features = storage.load_frame(Frame.FEATURES)
    joined = storage.load_frame(Frame.JOINED_TRIALS)

    decision_col = (
        "decision_issue_date" if "decision_issue_date" in joined.columns
        else "latest_decision_date"
    )
    label_cols = joined[
        [
            "trial_number",
            "cancelled",
            "petition_filing_date",
            "patent_number",
            decision_col,
            "termination_date",
        ]
    ].copy()
    label_cols["trial_number"] = label_cols["trial_number"].astype(str)
    label_cols["patent_number"] = label_cols["patent_number"].astype(str)
    label_cols["label_resolution_date"] = pd.to_datetime(
        label_cols[decision_col], errors="coerce"
    ).combine_first(
        pd.to_datetime(label_cols["termination_date"], errors="coerce")
    )
    label_cols = label_cols.drop(columns=[decision_col, "termination_date"])

    features["trial_number"] = features["trial_number"].astype(str)
    merged = features.merge(label_cols, on="trial_number", how="inner")
    mature_cut = TODAY - pd.Timedelta(days=MATURE_DAYS)
    merged = merged[pd.to_datetime(merged["petition_filing_date"]) <= mature_cut].copy()

    y = merged["cancelled"].astype(int)
    petition_dates = merged["petition_filing_date"]
    # Compute rolling categorical encodings over the full corpus before
    # the train/holdout split — strict-< T₀ gating in the encoders makes
    # this leakage-free and gives every row the richest possible pre-T₀
    # history regardless of fold/holdout membership.
    merged = attach_rolling_encodings(merged, y)
    X = merged.drop(columns=["trial_number", "cancelled"])

    X_train, X_test, y_train, y_test, _, _ = time_split(
        X, y, petition_dates, holdout_after=HOLDOUT_AFTER
    )

    pipeline = build_pipeline(MODELS[ModelName.RANDOM_FOREST]())
    pipeline.fit(X_train, y_train)
    return pipeline, X_test, y_test, joined


def _save(fig, name: str) -> None:
    out = FIG_DIR / name
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


def chart_01_roc(df: pd.DataFrame) -> None:
    fpr, tpr, _ = roc_curve(df["y_true"], df["y_proba"])
    auc_val = auc(fpr, tpr)
    fig, ax = plt.subplots(figsize=(7, 5.5))
    ax.plot(fpr, tpr, color="#0B2B4A", linewidth=2, label=f"Random Forest (AUC = {auc_val:.3f})")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="chance")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("ROC — Held-out tail")
    ax.legend(loc="lower right")
    ax.grid(alpha=0.3)
    _save(fig, "01_roc_curve.png")


def chart_02_pr(df: pd.DataFrame) -> None:
    p, r, _ = precision_recall_curve(df["y_true"], df["y_proba"])
    base = df["y_true"].mean()
    fig, ax = plt.subplots(figsize=(7, 5.5))
    ax.plot(r, p, color="#C94A2D", linewidth=2, label="Random Forest")
    ax.axhline(base, color="grey", linestyle="--", linewidth=1, label=f"base rate = {base:.3f}")
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Precision-Recall — Held-out tail")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.legend(loc="upper right")
    ax.grid(alpha=0.3)
    _save(fig, "02_pr_curve.png")


def chart_03_confusion(df: pd.DataFrame) -> None:
    y_pred = (df["y_proba"] >= 0.5).astype(int)
    cm = confusion_matrix(df["y_true"], y_pred)
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, cmap="Blues")
    labels = ["Not Cancelled", "Cancelled"]
    ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
    ax.set_xticklabels(labels); ax.set_yticklabels(labels)
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
    ax.set_title("Confusion Matrix — Random Forest @ threshold 0.5")
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f"{cm[i, j]:d}", ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else "black", fontsize=14)
    fig.colorbar(im, ax=ax, fraction=0.046)
    _save(fig, "03_confusion_matrix.png")


def chart_04_feature_importance(pipeline, top_n: int = 15) -> None:
    pre = pipeline.named_steps["preprocess"]
    model = pipeline.named_steps["model"]
    names = list(pre.get_feature_names_out())
    importances = model.feature_importances_
    idx = np.argsort(importances)[-top_n:]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(range(len(idx)), importances[idx], color="#0B2B4A")
    ax.set_yticks(range(len(idx)))
    ax.set_yticklabels([names[i] for i in idx])
    ax.set_xlabel("Importance (mean decrease in impurity)")
    ax.set_title(f"Top {top_n} Features — Random Forest")
    ax.grid(alpha=0.3, axis="x")
    _save(fig, "04_feature_importance.png")


def chart_05_probability_distribution(df: pd.DataFrame) -> None:
    pos = df.loc[df["y_true"] == 1, "y_proba"]
    neg = df.loc[df["y_true"] == 0, "y_proba"]
    bins = np.linspace(0, 1, 61)
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.hist(neg, bins=bins, density=True, alpha=0.55, color="#4C72B0",
            label=f"Actual: not cancelled  (n = {len(neg):,})")
    ax.hist(pos, bins=bins, density=True, alpha=0.55, color="#C94A2D",
            label=f"Actual: cancelled  (n = {len(pos):,})")
    ax.axvline(0.5, color="grey", linestyle=":", linewidth=1, label="Default threshold (0.5)")
    ax.set_xlabel("Predicted P(cancelled)")
    ax.set_ylabel("Density")
    ax.set_title("Predicted Probability Distribution by True Class")
    ax.legend()
    ax.grid(alpha=0.3)
    _save(fig, "05_probability_distribution.png")


def chart_06_cv_vs_holdout(metrics: dict) -> None:
    cv_mean = metrics["cv"]["roc_auc_mean"]
    cv_std = metrics["cv"]["roc_auc_std"]
    ho = metrics["held_out"]["roc_auc"]
    fig, ax = plt.subplots(figsize=(6.5, 5))
    xs = ["CV (mean ± std)", "Held-out tail"]
    vals = [cv_mean, ho]
    errs = [cv_std, 0.0]
    bars = ax.bar(xs, vals, yerr=errs, color=["#0B2B4A", "#C94A2D"], capsize=8, alpha=0.85)
    for b, v in zip(bars, vals, strict=True):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v:.3f}",
                ha="center", va="bottom", fontsize=10)
    ax.axhline(0.5, color="grey", linestyle="--", linewidth=1, label="chance")
    ax.set_ylabel("ROC-AUC")
    ax.set_ylim(0.4, max(0.75, ho + 0.1))
    ax.set_title("CV vs. Held-out ROC-AUC")
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    _save(fig, "06_cv_vs_holdout.png")


def chart_08_cancellation_by_year(joined: pd.DataFrame) -> None:
    df = joined[["petition_filing_date", "cancelled"]].copy()
    df["year"] = pd.to_datetime(df["petition_filing_date"]).dt.year
    by_year = df.groupby("year")["cancelled"].agg(["mean", "count"]).reset_index()
    by_year = by_year[by_year["count"] >= 20]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(by_year["year"], by_year["mean"], color="#0B2B4A", alpha=0.85)
    ax.set_xlabel("Petition filing year")
    ax.set_ylabel("Cancellation rate")
    ax.set_title("Cancellation Rate by Petition-Filing Year")
    ax.grid(alpha=0.3, axis="y")
    for x, m, n in zip(by_year["year"], by_year["mean"], by_year["count"], strict=True):
        ax.text(x, m + 0.005, f"n={n}", ha="center", va="bottom", fontsize=7, color="#555")
    _save(fig, "08_cancellation_rate_by_year.png")


def chart_09_threshold_sweep(df: pd.DataFrame) -> None:
    thresholds = np.linspace(0.05, 0.95, 91)
    precs, recs, f1s = [], [], []
    for t in thresholds:
        y_pred = (df["y_proba"] >= t).astype(int)
        p = precision_score(df["y_true"], y_pred, zero_division=0)
        r = recall_score(df["y_true"], y_pred, zero_division=0)
        precs.append(p); recs.append(r)
        f1s.append(2 * p * r / (p + r) if (p + r) else 0.0)
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    ax.plot(thresholds, precs, label="Precision", color="#0B2B4A", linewidth=2)
    ax.plot(thresholds, recs, label="Recall", color="#C94A2D", linewidth=2)
    ax.plot(thresholds, f1s, label="F1", color="#666", linewidth=2, linestyle="--")
    ax.axvline(0.5, color="grey", linestyle=":", linewidth=1, label="Default threshold (0.5)")
    ax.set_xlabel("Decision threshold")
    ax.set_ylabel("Score")
    ax.set_title("Precision / Recall / F1 vs. Threshold — Held-out tail")
    ax.legend()
    ax.grid(alpha=0.3)
    _save(fig, "09_threshold_sweep.png")


def main() -> None:
    pipeline, X_test, y_test, joined = _load_split_and_fit()
    y_proba = pipeline.predict_proba(X_test)[:, 1]
    df = pd.DataFrame({"y_true": y_test.values, "y_proba": y_proba})
    df.to_csv(PRED_PATH, index=False)

    metrics = json.loads(METRICS_PATH.read_text())

    chart_01_roc(df)
    chart_02_pr(df)
    chart_03_confusion(df)
    chart_04_feature_importance(pipeline)
    chart_05_probability_distribution(df)
    chart_06_cv_vs_holdout(metrics)
    chart_08_cancellation_by_year(joined)
    chart_09_threshold_sweep(df)


if __name__ == "__main__":
    main()
