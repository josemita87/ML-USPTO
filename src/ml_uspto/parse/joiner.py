"""Join trials ⨝ decisions ⨝ petitions ⨝ patents and attach the label."""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.parse.labels import build_labels
from ml_uspto.protocols.storage import Storage
from ml_uspto.schemas.models import JoinReport

logger = logging.getLogger(__name__)


def _column_to_dates(series: pd.Series) -> pd.Series:
    """Vectorized coercion of a column to `datetime.date | None`.

    Handles columns typed as `datetime64[ns]`, `object` carrying
    `date`, or strings.
    """
    coerced = pd.to_datetime(series, errors="coerce")
    return coerced.dt.date.where(coerced.notna(), None)


def join_all(
    storage: Storage,
    *,
    trials: pd.DataFrame,
    decisions: pd.DataFrame,
    petitions: pd.DataFrame,
    patents: pd.DataFrame,
    petition_texts: pd.DataFrame,
) -> tuple[pd.DataFrame, JoinReport]:
    """Build the labeled, petitioned, patent-attached, text-attached trial frame.

    Pure structural post-processing — feature engineering (T₀ leakage
    filtering, count aggregation, regex extraction over petition text,
    transforms) happens entirely downstream in
    `features.transforms.build_features`. The joined frame carries:
      - patent parallel-array columns from `Frame.PATENTS`
        (`event_codes`, `event_dates`, `assignment_received_dates`,
        `assignees_per_assignment`, …);
      - the raw `petition_text` column from `Frame.PETITION_TEXTS`
        (full pdfplumber-extracted text per trial).

    Steps:
      1. `build_labels(trials, decisions)` → labeled trials with
         `cancelled` (drops pending and label-unresolvable rows).
      2. Drop rows missing `petition_filing_date` or `patent_number` —
         required for the petitions merge.
      3. Inner-join with `petitions` on `trial_number`. Labeled trials
         without a petition row drop out implicitly.
      4. Cross-check `petition_filing_date` (proceedings) against
         `petition_filing_date_doc` (documents) on each merged row.
         Mismatches are logged per trial and counted on `JoinReport`;
         proceedings wins for all downstream filtering.
      5. Left-join with `patents` on `application_number`. Trials whose
         patent fetch failed carry NaN for every patent column — the
         features stage flags those rows via the
         `patent_features_missing` regime indicator.
      6. Left-join with `petition_texts` on `trial_number`. Trials
         whose petition PDF hasn't been fetched/extracted yet carry
         NaN for `petition_text`; the features-stage Tier A aggregator
         falls back to 0 on empty text.

    Args:
        storage: Backend forwarded to `build_labels` for the cached
            FWD-text label fallback.
        trials: Flattened proceedings frame.
        decisions: Flattened decisions frame.
        petitions: Frame of `Petition` rows from
            `parse.petitions.assemble_petitions`.
        patents: Flattened patent file-wrapper frame.
        petition_texts: Per-trial pdfplumber-extracted petition text
            from `Frame.PETITION_TEXTS` (columns: `trial_number`,
            `petition_text`).

    Returns:
        Tuple of (joined frame, `JoinReport`) describing input/output
        cardinalities and the T₀-mismatch count.
    """
    n_trials_input = len(trials)
    labeled = build_labels(trials, decisions, storage=storage)
    labeled = labeled.dropna(subset=["petition_filing_date", "patent_number"]).copy()
    n_trials_labeled = len(labeled)

    # Keep only the merge key + the two columns downstream actually consumes:
    # `petition_filing_date_doc` for the T₀ cross-check below, and
    # `petition_pdf_uri` as the handle for the deferred Tier 1/2 text-feature
    # stage (see `docs/features/admissible_documents_analysis.md`). Petition
    # metadata (title, category, document_id) carries no feature value and is
    # left in `petitions.parquet` rather than ballooning the joined frame.
    petition_cols = ["trial_number", "petition_pdf_uri", "petition_filing_date_doc"]
    petitions_slim = petitions[petition_cols].copy()
    petitions_slim["trial_number"] = petitions_slim["trial_number"].astype(str)
    labeled["trial_number"] = labeled["trial_number"].astype(str)

    joined = labeled.merge(petitions_slim, on="trial_number", how="inner")

    t0_series = _column_to_dates(joined["petition_filing_date"])
    t0_doc_series = _column_to_dates(joined["petition_filing_date_doc"])

    mismatch_mask = (
        t0_series.notna() & t0_doc_series.notna() & (t0_series != t0_doc_series)
    )
    n_t0_mismatch = int(mismatch_mask.sum())
    for trial, t0_doc, t0 in zip(
        joined.loc[mismatch_mask, "trial_number"],
        t0_doc_series[mismatch_mask],
        t0_series[mismatch_mask],
        strict=False,
    ):
        logger.warning(
            "T₀ mismatch for %s: documents=%s proceedings=%s — proceedings wins",
            trial, t0_doc, t0,
        )

    if not patents.empty:
        patents = patents.assign(application_number=patents["application_number"].astype(str))
        joined["application_number"] = joined["application_number"].astype(str)
        joined = joined.merge(patents, on="application_number", how="left")

    pt = petition_texts.copy()
    pt["trial_number"] = pt["trial_number"].astype(str)
    joined = joined.merge(pt, on="trial_number", how="left")

    report = JoinReport(
        n_trials_input=n_trials_input,
        n_trials_labeled=n_trials_labeled,
        n_petition_t0_mismatch=n_t0_mismatch,
        n_joined=len(joined),
    )
    logger.info(
        "Joined %d rows (%d labeled trials had no petition row); T₀ mismatch=%d",
        report.n_joined,
        report.n_trials_labeled - report.n_joined,
        report.n_petition_t0_mismatch,
    )
    return joined, report


__all__ = ["join_all"]
