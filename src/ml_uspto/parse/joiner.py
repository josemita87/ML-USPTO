"""Stage 4 — join trials ⨝ petitions ⨝ patent features.

Pure post-processing: reads the trials/decisions/petitions parquets plus the
cached raw patent payloads, runs `parse.labels.build_labels` to derive the
`cancelled` label, runs `features.patents.cleanse_at_t0` + `extract_features`
per (trial, app) to build T₀-safe patent features, and emits one row per
labeled trial that has a petition row.

Notes:
  - Patent fetch failures are silent: the patents driver writes only
    successes to `Stage.PATENTS`, and a cache miss here yields None
    patent features for that row (the trial still appears, just with
    nulls). Next cron run retries the missing fetch automatically.
  - T₀ cross-check: each merged row carries both `petition_filing_date`
    (proceedings) and `petition_filing_date_doc` (documents). Mismatches
    are logged once per trial and counted on `JoinReport`; proceedings
    wins for all downstream filtering.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from ml_uspto.protocols.storage import Storage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.features.patents import cleanse_at_t0, extract_features
from ml_uspto.parse.labels import build_labels
from ml_uspto.parse.patents import parse_patent_wrapper
from ml_uspto.schemas.models import JoinReport, PatentFeatures

logger = logging.getLogger(__name__)


def _patent_features_columns() -> list[str]:
    """Feature column order — stable for parquet schema.

    `trial_number` and `application_number` are joined-frame keys, not
    features; they live on the trials side and would collide on concat.
    """
    return [
        name
        for name in PatentFeatures.model_fields
        if name not in {"trial_number", "application_number"}
    ]


def _aggregate_one(
    storage: Storage,
    application_number: str,
    trial_number: str,
    petition_t0: date,
    grant_date: date | None,
) -> PatentFeatures | None:
    """Load cached patent payload for `application_number` and aggregate.

    Returns None if the payload is missing or cannot be aggregated.
    Populates `days_grant_to_petition` from the proceedings-side grant_date
    (the file wrapper has it as `grantDate` too, but proceedings-side is
    canonical for the join).
    """
    payload = storage.load_object(Stage.PATENTS, application_number)
    if payload is None:
        return None
    if "_fetch_error" in payload:
        # Legacy quarantine stub from before quarantines were removed.
        # Treat as missing; the next patents driver run will retry the fetch.
        return None

    try:
        wrapper = parse_patent_wrapper(payload)
        # Override with the proceedings-side number: canonical key for the
        # join even if the wrapper omits it.
        wrapper.application_number = application_number
        snapshot = cleanse_at_t0(
            wrapper,
            trial_number=trial_number,
            petition_filing_date=petition_t0,
        )
        features = extract_features(snapshot)
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "Aggregate failed for trial=%s app=%s: %s",
            trial_number,
            application_number,
            exc,
        )
        return None

    if grant_date is not None:
        days = (petition_t0 - grant_date).days
        features = features.model_copy(update={"days_grant_to_petition": days})
    return features


def _column_to_dates(series: pd.Series) -> pd.Series:
    """Coerce a column to `datetime.date | None` once, vectorized.

    Replaces per-row `to_date()` inside the join loop. Handles columns
    typed as `datetime64[ns]`, `object` carrying `date`, or strings.
    """
    coerced = pd.to_datetime(series, errors="coerce")
    return coerced.dt.date.where(coerced.notna(), None)


def join_all(
    storage: Storage,
    *,
    trials: pd.DataFrame,
    decisions: pd.DataFrame,
    petitions: pd.DataFrame,
) -> tuple[pd.DataFrame, JoinReport]:
    """Build the feature-ready joined frame.

    All inputs are loaded by the caller (`drivers/run_join.py`) — keeping I/O
    out of this function makes it cheap to unit-test on synthetic frames.

    Steps:
      1. `build_labels(trials, decisions)` → labeled trials with `cancelled`
         (drops pending and label-unresolvable rows).
      2. Drop rows missing `petition_filing_date` or `patent_number` —
         required for the petitions merge and patent-feature aggregation
         respectively. These are joiner-level pre-conditions, not label
         concerns.
      3. Inner-join with `petitions` on `trial_number`. Labeled trials
         without a petition row drop out implicitly.
      4. Cross-check `petition_filing_date` (proceedings) against
         `petition_filing_date_doc` (documents) on each merged row;
         proceedings wins.
      5. For each surviving (trial, app), look up the cached patent payload
         and run `parse_patent_wrapper` → `cleanse_at_t0` →
         `extract_features` with the proceedings-side T₀.
    """
    n_trials_input = len(trials)
    labeled = build_labels(trials, decisions, storage=storage)
    labeled = labeled.dropna(subset=["petition_filing_date", "patent_number"]).copy()
    n_trials_labeled = len(labeled)

    petition_cols = ["trial_number", "petition_pdf_uri", "petition_filing_date_doc"]
    petitions_slim = petitions[petition_cols].copy()
    petitions_slim["trial_number"] = petitions_slim["trial_number"].astype(str)
    labeled["trial_number"] = labeled["trial_number"].astype(str)

    joined = labeled.merge(petitions_slim, on="trial_number", how="inner")

    t0_series = _column_to_dates(joined["petition_filing_date"])
    t0_doc_series = _column_to_dates(joined["petition_filing_date_doc"])
    grant_series = _column_to_dates(joined["grant_date"])

    mismatch_mask = (
        t0_series.notna() & t0_doc_series.notna() & (t0_series != t0_doc_series)
    )
    n_t0_mismatch = int(mismatch_mask.sum())
    for trial, t0_doc, t0 in zip(
        joined.loc[mismatch_mask, "trial_number"],
        t0_doc_series[mismatch_mask],
        t0_series[mismatch_mask],
    ):
        logger.warning(
            "T₀ mismatch for %s: documents=%s proceedings=%s — proceedings wins",
            trial, t0_doc, t0,
        )

    feature_columns = _patent_features_columns()
    none_row = {col: None for col in feature_columns}
    feature_rows: list[dict[str, Any]] = []
    n_with_features = 0

    for app_raw, trial, t0, grant in zip(
        joined["application_number"], joined["trial_number"], t0_series, grant_series
    ):
        app = str(app_raw or "").strip()
        if not app or t0 is None:
            feature_rows.append(none_row)
            continue

        features = _aggregate_one(storage, app, str(trial), t0, grant)
        if features is None:
            feature_rows.append(none_row)
            continue

        dump = features.model_dump(mode="python")
        feature_rows.append({col: dump[col] for col in feature_columns})
        n_with_features += 1

    features_frame = pd.DataFrame(feature_rows, columns=feature_columns, index=joined.index)
    out = pd.concat([joined, features_frame], axis=1)

    report = JoinReport(
        n_trials_input=n_trials_input,
        n_trials_labeled=n_trials_labeled,
        n_petition_t0_mismatch=n_t0_mismatch,
        n_joined=len(out),
        n_with_patent_features=n_with_features,
    )
    logger.info(
        "Joined %d rows (%d labeled trials had no petition row); "
        "patent features: %d w/ %d w/o; T₀ mismatch=%d",
        report.n_joined,
        report.n_trials_labeled - report.n_joined,
        report.n_with_patent_features,
        report.n_joined - report.n_with_patent_features,
        report.n_petition_t0_mismatch,
    )
    return out, report


__all__ = ["join_all"]
