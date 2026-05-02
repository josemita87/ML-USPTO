"""FWD decision-record walking + cache-gap detection."""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.ingest.schemas.enums import FwdPdfCandidateColumn as Col
from ml_uspto.ingest.schemas.enums import Stage
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


def _iter_fwd_decisions(storage: Storage):
    """Yield FWD-original raw decision records.

    Reads `Stage.DECISIONS` raw JSON pages directly. The flattened
    `Frame.DECISIONS` parquet drops `documentData.fileDownloadURI`
    (no feature consumes it), but the gap-detector needs the URI to
    hand to the fetch driver, so we walk the raw cache.

    Restricted to *original* FWDs per
    `docs/scope/prediction_scope.md` §3.1 — on-remand and rehearing
    variants reference the remanded subset rather than the
    originally-challenged set, and would yield wrong labels.
    """
    for _page_key, payload in storage.iter_objects(Stage.DECISIONS.value):
        for record in payload.get("patentTrialDocumentDataBag") or []:
            doc = record.get("documentData") or {}
            doc_type = normalize_doctype(doc.get("documentTypeDescriptionText") or "")
            if doc_type not in FWD_ORIGINAL_DOCUMENT_TYPES:
                continue
            title = (doc.get("documentTitleText") or "").strip()
            if doc_type == LEGACY_FWD_DOCUMENT_TYPE and any(
                m in title.lower() for m in LEGACY_FWD_AMENDMENT_TITLE_MARKERS
            ):
                continue
            ident = doc.get("documentIdentifier")
            uri = doc.get("fileDownloadURI")
            trial = record.get("trialNumber")
            if not ident or not uri or not trial:
                continue
            issue = (record.get("decisionData") or {}).get("decisionIssueDate") or ""
            yield {
                Col.TRIAL_NUMBER.value: str(trial),
                Col.DOCUMENT_IDENTIFIER.value: str(ident),
                Col.FILE_DOWNLOAD_URI.value: str(uri),
                Col.DOCUMENT_TITLE.value: title,
                Col.DECISION_ISSUE_DATE.value: issue,
            }


def enumerate_missing_fwd_pdfs(
    storage: Storage,
    *,
    trials: pd.DataFrame,
) -> pd.DataFrame:
    """Return candidate rows whose label requires a not-yet-cached FWD text blob.

    Used by `drivers/run_ingest_decision_texts.py` to derive the next
    fetch work list from current cache state. A permanently-broken PDF
    will reappear on every cron cycle; at weekly-delta scale (~30–50
    candidates) that's negligible against the 1.2M/wk PDF bucket, so
    we don't track failures.

    Args:
        storage: Backend over the raw `Stage.DECISIONS` cache and the
            `Stage.DECISION_TEXTS` blob store.
        trials: Flattened trials frame (used to filter by trial type
            and status).

    Returns:
        Candidate frame with one row per unresolvable, not-yet-cached
        FWD. Exclusions applied in order:
          1. Non-IPR trials (we predict on IPR only).
          2. `trial_status ∈ NON_FWD_LABEL_{0,1}_STATUSES` (already 0/1).
          3. `extract_outcome(document_title) is not None`
             (title-resolvable).
          4. Text already cached at
             `Stage.DECISION_TEXTS / <doc_id>.txt`.
    """
    cols = [c.value for c in Col]
    candidates = pd.DataFrame(list(_iter_fwd_decisions(storage)), columns=cols)
    n_total = len(candidates)
    if candidates.empty:
        return candidates

    trials_view = trials[["trial_number"]].copy()
    trials_view["trial_number"] = trials_view["trial_number"].astype(str)
    for col in ("trial_status", "trial_type"):
        trials_view[col] = (
            trials[col].astype(object) if col in trials.columns else None
        )
    trials_view = trials_view.drop_duplicates(subset="trial_number")
    candidates = candidates.merge(trials_view, on=Col.TRIAL_NUMBER.value, how="left")

    is_ipr = candidates["trial_type"] == TrialType.IPR.value
    status_resolvable_set = NON_FWD_LABEL_0_STATUSES | NON_FWD_LABEL_1_STATUSES
    is_status_resolvable = candidates["trial_status"].isin(status_resolvable_set)
    title_label = candidates[Col.DOCUMENT_TITLE.value].fillna("").map(extract_outcome)
    is_title_resolvable = title_label.notna()

    keep = is_ipr & ~is_status_resolvable & ~is_title_resolvable
    after_labels = candidates.loc[keep, cols].copy()
    n_after_labels = len(after_labels)

    cached_keys = set(storage.iter_blob_keys(Stage.DECISION_TEXTS.value, "txt"))
    after_cache = after_labels.loc[
        ~after_labels[Col.DOCUMENT_IDENTIFIER.value].astype(str).isin(cached_keys)
    ].copy()
    n_final = len(after_cache)

    logger.info(
        "FWD-PDF gap detector: %d original-FWD rows -> %d unresolvable-by-status/title "
        "-> %d not cached",
        n_total, n_after_labels, n_final,
    )
    return after_cache.reset_index(drop=True)


__all__ = ["enumerate_missing_fwd_pdfs"]
