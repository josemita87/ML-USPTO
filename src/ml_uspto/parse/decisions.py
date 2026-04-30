"""FWD decision label resolution + cache-gap detection.

Two public functions, one domain — both deal with USPTO PTAB Final
Written Decisions:

  - `extract_outcome(text)` — runs the FWD outcome regex against either a
    `documentTitleText` or extracted PDF cover-page text and returns the
    binary `cancelled` label (1 / 0 / None). Pure function; consumed by
    `parse.labels` (label pipeline) and by `enumerate_missing_fwd_pdfs`
    below (gap probing).

  - `enumerate_missing_fwd_pdfs(...)` — walks the raw `Stage.DECISIONS`
    JSON cache and returns the candidate rows whose `cancelled` label is
    unresolvable by status + title, so a not-yet-cached PDF cover page is
    the only label source. Used by `drivers/run_fetch_decision_pdfs.py`.

Per `docs/scope/prediction_scope.md` §3.1, both paths are restricted to
*original* FWDs — on-remand and rehearing variants reference the
remanded subset rather than the originally-challenged set, and would
yield wrong labels.

The full title/cover-page phrasings the regex covers:

    Determining All Challenged Claims Unpatentable          → 1
    Determining No Challenged Claims Unpatentable           → 0
    Determining Some Challenged Claims Unpatentable         → 0
    Determining Challenged Claims <list> Unpatentable       → 0
    Determining Challenged Claim(s) Unpatentable            → 1

The (regex, label) list lives in `config/labels.yaml::fwd_pdf_outcome.patterns`
and is exposed via `schemas.patterns.FWD_PDF_OUTCOME_PATTERNS`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from ml_uspto.protocols.storage import Storage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.schemas.enums import FwdPdfCandidateColumn as Col
from ml_uspto.schemas.constants import (
    FWD_DECISION_TYPE_MARKER,
    FWD_ORIGINAL_MARKER,
    FWD_PDF_COVER_PAGE_SEARCH_CHARS,
    NON_FWD_LABEL_0_STATUSES,
    NON_FWD_LABEL_1_STATUSES,
)
from ml_uspto.schemas.enums import TrialType
from ml_uspto.schemas.patterns import FWD_PDF_OUTCOME_PATTERNS

logger = logging.getLogger(__name__)


def extract_outcome(text: str) -> int | None:
    """Return the binary `cancelled` label from FWD title or PDF text.

    Inspects the first `FWD_PDF_COVER_PAGE_SEARCH_CHARS` of `text` and
    runs `FWD_PDF_OUTCOME_PATTERNS` in order; returns the first match's
    label. Returns `None` if no pattern matches — caller's choice whether
    to drop, quarantine, or fall back (image-scan PDFs, Motion-to-Amend
    rulings without a "Determining" cover line, malformed text).

    Same head-window applies to titles (which are far shorter than the
    window) and to extracted PDF cover-page text.
    """
    head = text[:FWD_PDF_COVER_PAGE_SEARCH_CHARS]
    for entry in FWD_PDF_OUTCOME_PATTERNS:
        if entry.pattern.search(head) is not None:
            return entry.label
    return None


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


def _recently_failed(
    failures: pd.DataFrame | None, retry_after_days: int
) -> set[str]:
    """doc_ids whose latest failure is within the retry-after window.

    The fetch driver appends a row per failed download to
    `Frame.DECISION_PDF_FAILURES`. The gap-detector treats those as
    unavailable for `retry_after_days` so a single 5xx doesn't permanently
    quarantine a PDF — but the same doc_id won't be retried every cron
    cycle either.
    """
    if failures is None or failures.empty:
        return set()
    if Col.DOCUMENT_IDENTIFIER.value not in failures.columns or "failed_at" not in failures.columns:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retry_after_days)
    failed_at = pd.to_datetime(failures["failed_at"], errors="coerce", utc=True)
    recent = failures.loc[failed_at >= cutoff, Col.DOCUMENT_IDENTIFIER.value].dropna()
    return {str(d).strip() for d in recent if str(d).strip()}


def enumerate_missing_fwd_pdfs(
    storage: Storage,
    *,
    trials: pd.DataFrame,
    failures: pd.DataFrame | None = None,
    retry_after_days: int = 7,
) -> pd.DataFrame:
    """Return candidate rows whose label requires a not-yet-cached FWD PDF.

    Exclusion layers, applied in order:
      1. Non-IPR trials (we predict on IPR only).
      2. trial_status ∈ NON_FWD_LABEL_{0,1}_STATUSES — already 0/1.
      3. extract_outcome(document_title) is not None — title-resolvable.
      4. PDF already cached at `Stage.DECISION_PDFS / <doc_id>.pdf`.
      5. doc_id has a failure entry within `retry_after_days`.
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

    cached_keys = set(storage.iter_blob_keys(Stage.DECISION_PDFS.value, "pdf"))
    after_cache = after_labels.loc[
        ~after_labels[Col.DOCUMENT_IDENTIFIER.value].astype(str).isin(cached_keys)
    ].copy()
    n_after_cache = len(after_cache)

    failed_recently = _recently_failed(failures, retry_after_days)
    if failed_recently:
        after_cache = after_cache.loc[
            ~after_cache[Col.DOCUMENT_IDENTIFIER.value].isin(failed_recently)
        ]
    n_final = len(after_cache)

    logger.info(
        "FWD-PDF gap detector: %d original-FWD rows -> %d unresolvable-by-status/title "
        "-> %d not cached -> %d after retry-window (retry_after_days=%d)",
        n_total, n_after_labels, n_after_cache, n_final, retry_after_days,
    )
    return after_cache.reset_index(drop=True)


__all__ = ["extract_outcome", "enumerate_missing_fwd_pdfs"]
