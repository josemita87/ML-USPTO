"""Petition-PDF cache-gap detection + cached-blob → text-frame assembly."""

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

    candidates = petitions[list(PETITION_GAP_CANDIDATE_COLUMNS)].copy()
    candidates["trial_number"] = candidates["trial_number"].astype(str)

    n_total = len(candidates)
    has_uri = candidates["petition_pdf_uri"].fillna("").astype(str).str.strip().ne("")
    candidates = candidates.loc[has_uri].copy()
    n_with_uri = len(candidates)

    cached_keys = set(storage.iter_blob_keys(Stage.PETITION_TEXTS.value, "txt"))
    candidates = candidates.loc[
        ~candidates["trial_number"].isin(cached_keys)
    ].copy()
    n_final = len(candidates)

    logger.info(
        "Petition-PDF gap detector: %d petition rows -> %d with URI -> %d not cached",
        n_total, n_with_uri, n_final,
    )
    return candidates.reset_index(drop=True)


def build_petition_texts_frame(storage: Storage) -> pd.DataFrame:
    """Walk every cached petition-text blob → frame for `Frame.PETITION_TEXTS`.

    Idempotent over the blob store: no HTTP, no PDF parsing. Output
    schema is `(trial_number, petition_text)` — the joiner left-joins
    on `trial_number`, the features stage runs Tier A regexes over
    `petition_text`.
    """
    rows: list[dict[str, str]] = []
    for trial in storage.iter_blob_keys(Stage.PETITION_TEXTS.value, "txt"):
        payload = storage.load_blob(Stage.PETITION_TEXTS.value, trial, "txt")
        if payload is None:
            # Race window between iter_blob_keys and load_blob — skip.
            logger.warning("Petition text blob disappeared mid-iteration: %s", trial)
            continue
        rows.append(
            {
                "trial_number": trial,
                "petition_text": payload.decode("utf-8", errors="replace"),
            }
        )
    return pd.DataFrame(rows, columns=["trial_number", "petition_text"])


__all__ = ["build_petition_texts_frame", "enumerate_missing_petition_pdfs"]
