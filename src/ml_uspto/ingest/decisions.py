"""FWD decision-record cache-gap detection."""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.ingest.schemas.enums import FwdPdfCandidateColumn, Stage
from ml_uspto.parse.labels import extract_outcome
from ml_uspto.protocols.storage import Storage
from ml_uspto.schemas.constants import (
    FWD_ORIGINAL_DOCUMENT_TYPES,
    LEGACY_FWD_AMENDMENT_TITLE_MARKERS,
    LEGACY_FWD_DOCUMENT_TYPE,
    NON_FWD_LABEL_0_STATUSES,
    NON_FWD_LABEL_1_STATUSES,
    normalize_doctype,
)
from ml_uspto.schemas.enums import TrialType

logger = logging.getLogger(__name__)


def enumerate_missing_fwd_pdfs(
    storage: Storage,
    *,
    trials: pd.DataFrame,
    decisions: pd.DataFrame,
) -> pd.DataFrame:
    """Return candidate rows whose label requires a not-yet-cached FWD text blob.

    Used by `drivers/run_ingest_decision_texts.py` to derive the next
    fetch work list from current cache state. A permanently-broken PDF
    will reappear on every cron cycle; at weekly-delta scale (~30–50
    candidates) that's negligible against the 1.2M/wk PDF bucket, so
    we don't track failures.

    Restricted to *original* FWDs per `docs/scope/prediction_scope.md`
    §3.1 — on-remand and rehearing variants reference the remanded
    subset rather than the originally-challenged set, and would yield
    wrong labels.

    Args:
        storage: Backend over the `Stage.DECISION_TEXTS` blob store.
        trials: Flattened trials frame (`Frame.TRIALS`), used to filter
            by trial type and status.
        decisions: Flattened decisions frame (`Frame.DECISIONS`), source
            of FWD candidates and download URIs.

    Returns:
        Candidate frame keyed by `FwdPdfCandidateColumn`. Exclusions
        applied in order:
          1. Non-original-FWD docs (drops `Final Decision` rows whose
             title marks them as amendment FWDs, plus on-remand and
             rehearing variants).
          2. Rows missing trial number, document identifier, or URI.
          3. Non-IPR trials (we predict on IPR only).
          4. `trial_status ∈ NON_FWD_LABEL_{0,1}_STATUSES` (already 0/1).
          5. `extract_outcome(document_title) is not None`
             (title-resolvable).
          6. Text already cached at
             `Stage.DECISION_TEXTS / <doc_id>.txt`.
    """
    cols = list(FwdPdfCandidateColumn)
    if decisions.empty:
        return pd.DataFrame(columns=cols)
    decisions = decisions.dropna(
        subset=["trial_number", "document_identifier", "file_download_uri", "document_type", "document_title"]
    )

    # Filter out non-original FWD or ammendments from the candidate set
    norm_type = decisions["document_type"].map(normalize_doctype)
    title_lower = decisions["document_title"].str.lower()
    is_fwd_original = norm_type.isin(FWD_ORIGINAL_DOCUMENT_TYPES)
    is_legacy_amendment = (norm_type == LEGACY_FWD_DOCUMENT_TYPE) & title_lower.apply(
        lambda t: any(m in t for m in LEGACY_FWD_AMENDMENT_TITLE_MARKERS)
    )
    candidates = decisions.loc[is_fwd_original & ~is_legacy_amendment, cols]

    n_total = len(candidates)
    if candidates.empty:
        return candidates.reset_index(drop=True)

    cached_keys = set(storage.iter_blob_keys(Stage.DECISION_TEXTS, "txt"))

    # We need the trials df to validate the status & title conditions below
    trials_view = trials[["trial_number", "trial_status", "trial_type"]].drop_duplicates(subset="trial_number")
    candidates = candidates.merge(trials_view, on=FwdPdfCandidateColumn.TRIAL_NUMBER, how="left")

    is_ipr = candidates["trial_type"] == TrialType.IPR.value
    ## Whether the IPR had an outcome, and did not reach final written decision (i.e discretionary denial)
    is_status_resolvable = candidates["trial_status"].isin(NON_FWD_LABEL_0_STATUSES | NON_FWD_LABEL_1_STATUSES)
    ## Whether the title already provides us with the final written decision outcome.
    is_title_resolvable = candidates[FwdPdfCandidateColumn.DOCUMENT_TITLE].map(extract_outcome).notna()
    ## Whether this FWD has already been downloaded (cached)
    is_cached = candidates[FwdPdfCandidateColumn.DOCUMENT_IDENTIFIER].isin(cached_keys)

    unresolvable = is_ipr & ~is_status_resolvable & ~is_title_resolvable
    result = candidates.loc[unresolvable & ~is_cached, cols]

    logger.info(
        "FWD-PDF gap detector: %d original-FWD rows -> %d unresolvable-by-status/title "
        "-> %d not cached",
        n_total, int(unresolvable.sum()), len(result),
    )
    return result.reset_index(drop=True)


__all__ = ["enumerate_missing_fwd_pdfs"]
