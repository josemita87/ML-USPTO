"""Stage 4 — join trials ⨝ petitions ⨝ patent features.

Pure post-processing: reads the four stage 1–3 parquets plus the cached raw
patent payloads, runs `parse.labels.build_labels` to derive the
`cancelled` label, runs `features.patents.cleanse_at_t0` + `extract_features` per
(trial, app) to build T₀-safe patent features, and emits one row per labeled
non-quarantined trial.

Quarantine handling:
  - Petition quarantine (`petition_quarantine.parquet`) — trials with no
    pickable petition. Excluded entirely (no petition T₀ → no aggregation).
  - Patent quarantine (`patent_quarantine.parquet`) — applications whose
    file-wrapper fetch failed. Trial is kept, but its patent_features are
    None and downstream models must handle missingness.
  - Apps not yet fetched (cache miss) — also yield None patent_features. The
    join doesn't fetch; run `drivers/run_ingest_patents.py` first.

Correctness check returned alongside the frame:
  `expected_rows == len(labeled_trials) − len(petition_quarantine ∩ labeled_trials)`.
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
from ml_uspto.parse.utils import to_date
from ml_uspto.schemas.enums import Frame
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

    Returns None if the payload is missing, is a quarantine error stub, or
    cannot be aggregated. Populates `days_grant_to_petition` from the
    proceedings-side grant_date (the file wrapper has it as `grantDate`
    too, but proceedings-side is canonical for the join).
    """
    payload = storage.load_object(Stage.PATENTS, application_number)
    if payload is None:
        return None
    if "_fetch_error" in payload:
        return None

    try:
        wrapper = parse_patent_wrapper(payload)
        # Override with the proceedings-side number, which is the
        # canonical key for the join even if the wrapper omits it.
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


def _quarantined_apps(patent_quarantine: pd.DataFrame) -> set[str]:
    if patent_quarantine.empty or "application_number" not in patent_quarantine.columns:
        return set()
    return {
        str(a).strip()
        for a in patent_quarantine["application_number"].dropna()
        if str(a).strip()
    }


def _quarantined_trials(petition_quarantine: pd.DataFrame) -> set[str]:
    if petition_quarantine.empty or "trial_number" not in petition_quarantine.columns:
        return set()
    return {
        str(t).strip()
        for t in petition_quarantine["trial_number"].dropna()
        if str(t).strip()
    }


def join_all(
    storage: Storage,
    *,
    trials: pd.DataFrame,
    decisions: pd.DataFrame,
    petitions: pd.DataFrame,
    petition_quarantine: pd.DataFrame,
    patent_quarantine: pd.DataFrame,
) -> tuple[pd.DataFrame, JoinReport]:
    """Build the feature-ready joined frame.

    All inputs are loaded by the caller (`drivers/run_join.py`) — keeping I/O
    out of this function makes it cheap to unit-test on synthetic frames.

    Steps:
      1. `build_labels(trials, decisions)` → labeled trials with `cancelled`,
         excluding pending and dropping rows missing `petition_filing_date`
         or `patent_number`.
      2. Inner-join with `petitions` on `trial_number`. Petition-quarantined
         trials are absent from `petitions` and so are dropped.
      3. For each surviving (trial, app), look up the cached patent payload
         and run `parse_patent_wrapper` → `cleanse_at_t0` →
         `extract_features` with the proceedings-side T₀.
    """
    n_trials_input = len(trials)
    labeled = build_labels(trials, decisions, storage=storage)
    n_trials_labeled = len(labeled)

    petition_qn = _quarantined_trials(petition_quarantine)
    patent_qn = _quarantined_apps(patent_quarantine)

    petition_cols = [
        "trial_number",
        "petition_pdf_uri",
        "petition_filing_date_doc",
    ]
    petitions_slim = petitions[petition_cols].copy()
    petitions_slim["trial_number"] = petitions_slim["trial_number"].astype(str)
    labeled["trial_number"] = labeled["trial_number"].astype(str)

    joined = labeled.merge(petitions_slim, on="trial_number", how="inner")
    joined["petition_filing_date_doc"] = pd.to_datetime(
        joined["petition_filing_date_doc"], errors="coerce"
    )

    feature_columns = _patent_features_columns()
    feature_rows: list[dict[str, Any]] = []
    n_with_features = 0
    for row in joined.itertuples(index=False):
        app = str(row.application_number or "").strip()
        trial = str(row.trial_number)
        t0 = to_date(row.petition_filing_date)
        grant = to_date(row.grant_date)

        if not app or app in patent_qn or t0 is None:
            feature_rows.append({col: None for col in feature_columns})
            continue

        features = _aggregate_one(storage, app, trial, t0, grant)
        if features is None:
            feature_rows.append({col: None for col in feature_columns})
            continue

        dump = features.model_dump(mode="python")
        feature_rows.append({col: dump[col] for col in feature_columns})
        n_with_features += 1

    features_frame = pd.DataFrame(feature_rows, columns=feature_columns, index=joined.index)
    out = pd.concat([joined, features_frame], axis=1)

    report = JoinReport(
        n_trials_input=n_trials_input,
        n_trials_labeled=n_trials_labeled,
        n_petition_quarantine=len(petition_qn),
        n_patent_quarantine=len(patent_qn),
        n_joined=len(out),
        n_with_patent_features=n_with_features,
        n_without_patent_features=len(out) - n_with_features,
    )
    logger.info(
        "Joined %d rows (%d w/ patent features, %d w/o)",
        report.n_joined,
        report.n_with_patent_features,
        report.n_without_patent_features,
    )
    return out, report


__all__ = ["join_all"]
