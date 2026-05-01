"""FWD decision-record walking + cache-gap detection.

`enumerate_missing_fwd_pdfs(...)` walks the raw `Stage.DECISIONS` JSON
cache and returns the candidate rows whose `cancelled` label is
unresolvable by status + title, so a not-yet-cached FWD text blob is
the only label source. Used by `drivers/run_ingest_decision_texts.py`.

Lives in `ingest/` rather than `parse/` because it doesn't parse — it
derives a next-fetch work list from current cache state. It consults
`parse.labels.extract_outcome` to decide title-resolvability, but the
output is a candidate frame for the fetcher, not parsed records.

Per `docs/scope/prediction_scope.md` §3.1, this is restricted to
*original* FWDs — on-remand and rehearing variants reference the
remanded subset rather than the originally-challenged set, and would
yield wrong labels.
"""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.ingest.schemas.enums import FwdPdfCandidateColumn as Col
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.labels import extract_outcome
from ml_uspto.protocols.storage import Storage
from ml_uspto.schemas.constants import (
    FWD_DECISION_TYPE_MARKER,
    FWD_ORIGINAL_MARKER,
    NON_FWD_LABEL_0_STATUSES,
    NON_FWD_LABEL_1_STATUSES,
)
from ml_uspto.schemas.enums import TrialType

logger = logging.getLogger(__name__)


def _iter_fwd_decisions(storage: Storage):
    """Yield FWD-original raw decision records.

    Reads `Stage.DECISIONS` raw JSON pages directly. The flattened
    `Frame.DECISIONS` parquet drops `documentData.fileDownloadURI` (no
    feature consumes it), but the gap-detector needs the URI to hand to
    the fetch driver, so we walk the raw cache.
    """
    fwd_marker = FWD_DECISION_TYPE_MARKER.lower()
    orig_marker = FWD_ORIGINAL_MARKER.lower()
    for _page_key, payload in storage.iter_objects(Stage.DECISIONS.value):
        for record in payload.get("patentTrialDocumentDataBag") or []:
            doc = record.get("documentData") or {}
            doc_type = (doc.get("documentTypeDescriptionText") or "").lower()
            if fwd_marker not in doc_type or orig_marker not in doc_type:
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
                Col.DOCUMENT_TITLE.value: (doc.get("documentTitleText") or "").strip(),
                Col.DECISION_ISSUE_DATE.value: issue,
            }


def enumerate_missing_fwd_pdfs(
    storage: Storage,
    *,
    trials: pd.DataFrame,
) -> pd.DataFrame:
    """Return candidate rows whose label requires a not-yet-cached FWD text blob.

    Exclusion layers, applied in order:
      1. Non-IPR trials (we predict on IPR only).
      2. trial_status ∈ NON_FWD_LABEL_{0,1}_STATUSES — already 0/1.
      3. extract_outcome(document_title) is not None — title-resolvable.
      4. Text already cached at `Stage.DECISION_TEXTS / <doc_id>.txt`.

    A permanently-broken PDF will reappear on every cron cycle; at
    weekly-delta scale (~30–50 candidates) that's negligible against the
    1.2M/wk PDF bucket, so we don't track failures.
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
        "FWD-text gap detector: %d original-FWD rows -> %d unresolvable-by-status/title "
        "-> %d not cached",
        n_total, n_after_labels, n_final,
    )
    return after_cache.reset_index(drop=True)


__all__ = ["enumerate_missing_fwd_pdfs"]
