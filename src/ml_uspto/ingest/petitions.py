"""Petition-PDF cache-gap detection."""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.ingest.schemas.constants import PETITION_GAP_CANDIDATE_COLUMNS
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.protocols.storage import Storage

logger = logging.getLogger(__name__)


def enumerate_missing_petition_pdfs(
    storage: Storage,
    *,
    petitions: pd.DataFrame,
) -> pd.DataFrame:
    """Return petition rows whose extracted text is not yet cached.

    Cold start (~17.8K rows) and weekly cron (small deltas as new
    petitions file) run the same code path. The petitions frame is
    already filtered to one row per trial-with-petition by
    `parse.petitions.assemble_petitions`; we only drop rows whose
    `petition_pdf_uri` is missing/blank and rows whose extracted text
    is already on disk at `Stage.PETITION_TEXTS / <trial_number>.txt`.

    Args:
        storage: Backend over the `Stage.PETITION_TEXTS` blob store.
        petitions: Frame loaded from `Frame.PETITIONS` (one row per
            trial-with-petition).

    Returns:
        Candidate frame with columns `trial_number`,
        `petition_pdf_uri`, `petition_filing_date_doc`. Empty frame
        (typed) when nothing is missing.
    """
    if petitions.empty:
        return pd.DataFrame({c: [] for c in PETITION_GAP_CANDIDATE_COLUMNS})

    n_total = len(petitions)
    candidates = petitions[list(PETITION_GAP_CANDIDATE_COLUMNS)].dropna(
        subset=list(PETITION_GAP_CANDIDATE_COLUMNS)
    )
    candidates = candidates.loc[candidates["petition_pdf_uri"].str.strip().ne("")]
    n_with_uri = len(candidates)

    cached_keys = set(storage.iter_blob_keys(Stage.PETITION_TEXTS, "txt"))
    candidates = candidates.loc[~candidates["trial_number"].isin(cached_keys)]
    n_final = len(candidates)

    logger.info(
        "Petition-PDF gap detector: %d petition rows -> %d with URI -> %d not cached",
        n_total, n_with_uri, n_final,
    )
    return candidates.reset_index(drop=True)


__all__ = ["enumerate_missing_petition_pdfs"]
