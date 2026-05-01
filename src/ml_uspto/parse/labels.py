"""Build the binary `cancelled` label per `docs/scope/prediction_scope.md` §3.

Target = 1 iff the trial's *original* Final Written Decision held all
challenged claims unpatentable. Everything else (institution denied,
discretionary denial, settled, procedurally terminated, FWD where any claim
survived) is 0. Trials still pending are excluded — no terminal outcome,
nothing to label.

Per `prediction_scope.md` §3.1, only the original FWD is used as a label
source; on-remand and rehearing variants are dropped at this stage. See
the doc for the rationale and the trade-off (~5% of FWD trials have
amendments; expected label noise ≪1%).

Status/outcome taxonomies live in `config/labels.yaml` and are exposed via
`ml_uspto.schemas.constants` so they can be revised without code changes.

Label resolution layers, applied in order — each only fills rows the
previous layer left unresolved:
  1. **Status-based.** `trial_status ∈ NON_FWD_LABEL_{0,1}_STATUSES` →
     direct 0/1 (covers Institution Denied, Settled, Adverse Judgment, …).
  2. **`document_title` regex.** ~41% of original FWDs encode the
     granular ruling directly in the API-returned title text
     ("Determining All Challenged Claims Unpatentable"); `extract_outcome`
     applies the FWD outcome regex to the title.
  3. **Cached FWD-text fallback.** For trials still unresolved, load the
     cached FWD text blob (`Stage.DECISION_TEXTS / <document_identifier>.txt`,
     produced at fetch-time from the PDF) and run the same regex. Skipped
     silently when `storage=None` (unit-test mode) or when the text isn't
     cached. Backfilling the remaining FWD texts requires
     `drivers/run_ingest_decision_texts.py` to widen the download set.

The empirical sentinel layer (matching `decisionData.trialOutcomeCategory`
against `ALL_CLAIMS_UNPATENTABLE_OUTCOMES`) was removed in 2026-04 after
zero matches in the 2,180-row cold-run; if USPTO ever populates that
field, re-add it before layer 2 with one frozenset lookup.

Scope: this module builds *labels only*. It does not coerce dates,
filter on `petition_filing_date`/`patent_number`, or normalize feature
columns — those are joiner/feature-pipeline concerns. The IPR-only
filter is enforced server-side by `fetch_proceedings`, so we trust
upstream rather than re-filter here. Trials whose label is unresolvable
after all three layers are dropped; everything else flows through to
`parse.joiner` with a fully-labeled `cancelled` int column.
"""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.protocols.storage import Storage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.schemas.constants import (
    FWD_DECISION_TYPE_MARKER,
    FWD_ORIGINAL_MARKER,
    FWD_PDF_COVER_PAGE_SEARCH_CHARS,
    NON_FWD_LABEL_0_STATUSES,
    NON_FWD_LABEL_1_STATUSES,
    PENDING_STATUSES,
)
from ml_uspto.schemas.patterns import FWD_PDF_OUTCOME_PATTERNS

logger = logging.getLogger(__name__)


def extract_outcome(text: str) -> int | None:
    """Return the binary `cancelled` label from FWD title or opinion text.

    Inspects the first `FWD_PDF_COVER_PAGE_SEARCH_CHARS` of `text` and
    runs `FWD_PDF_OUTCOME_PATTERNS` in order; returns the first match's
    label. Returns `None` if no pattern matches — caller's choice whether
    to drop, quarantine, or fall back (image-scan PDFs, Motion-to-Amend
    rulings without a "Determining" cover line, malformed text).

    The head-window cap is a *correctness* guard — it stops a stray
    "Determining …" inside a citation deeper in the opinion from
    overriding the cover-page ruling. Same window applies to titles
    (which are far shorter than the window) and to full opinion text.
    """
    head = text[:FWD_PDF_COVER_PAGE_SEARCH_CHARS]
    for entry in FWD_PDF_OUTCOME_PATTERNS:
        if entry.pattern.search(head) is not None:
            return entry.label
    return None


def _identify_terminating_fwd(decisions: pd.DataFrame) -> pd.DataFrame:
    """Return one row per trial with the terminating-FWD metadata needed
    for label resolution: `terminating_outcome`, `document_title`, and
    `document_identifier` (for text-blob lookup).

    Filters to *original* FWDs only — `documentTypeDescriptionText`
    matching both `FWD_DECISION_TYPE_MARKER` ("Final Written Decision",
    case-insensitive) and `FWD_ORIGINAL_MARKER` ("original"). On-remand,
    rehearing, and Director-remand variants are dropped per
    `docs/scope/prediction_scope.md` §3.1: their cover-page outcomes
    refer to the remanded/rehearing subset, not the originally-challenged
    set, so they're not valid label sources.
    """
    out_cols = ["trial_number", "terminating_outcome", "document_title", "document_identifier"]
    empty = pd.DataFrame({c: [] for c in out_cols})
    if "document_type" not in decisions.columns or decisions.empty:
        return empty

    doc_type = decisions["document_type"].fillna("")
    is_fwd = doc_type.str.contains(FWD_DECISION_TYPE_MARKER, case=False, regex=False)
    is_original = doc_type.str.contains(FWD_ORIGINAL_MARKER, case=False, regex=False)
    fwds = decisions[is_fwd & is_original].copy()
    n_dropped = int((is_fwd & ~is_original).sum())
    if n_dropped:
        logger.info(
            "Dropped %d non-original FWD rows (on-remand / rehearing) per §3.1",
            n_dropped,
        )
    if fwds.empty:
        return empty

    fwds["decision_issue_date"] = pd.to_datetime(
        fwds["decision_issue_date"], errors="coerce"
    )
    # An "original" FWD is in principle unique per trial, but if a trial
    # ends up with multiple original-tagged rows (data drift), pick the
    # latest issue date — same heuristic as before, now within originals.
    idx = fwds.groupby("trial_number")["decision_issue_date"].idxmax()
    src_cols = ["trial_number", "trial_outcome"]
    if "document_title" in fwds.columns:
        src_cols.append("document_title")
    if "document_identifier" in fwds.columns:
        src_cols.append("document_identifier")
    terminating = fwds.loc[idx, src_cols].rename(
        columns={"trial_outcome": "terminating_outcome"}
    )
    # Backfill any column the upstream parquet doesn't carry (e.g. a stale
    # decisions.parquet predating the document_identifier yaml entry) with
    # NaN — the cached-text fallback layer just no-ops on those rows.
    for col in ("document_title", "document_identifier"):
        if col not in terminating.columns:
            terminating[col] = pd.NA
    return terminating[out_cols]


def build_labels(
    proceedings: pd.DataFrame,
    decisions: pd.DataFrame,
    storage: Storage | None = None,
) -> pd.DataFrame:
    """Attach a `cancelled` int column to `proceedings`, drop unlabelable rows.

    Returns one row per labeled trial — same columns as `proceedings` plus
    the FWD metadata merged in (`terminating_outcome`, `document_title`,
    `document_identifier`) plus the `cancelled` int. Rows are dropped iff:
      - status ∈ PENDING_STATUSES (no terminal outcome), or
      - the three-layer cascade fails to resolve the label.

    When `storage` is given, the cached-text fallback runs against
    `Stage.DECISION_TEXTS`; when None, only status + title extraction
    are attempted.
    """
    logger.info("Raw proceedings: %d", len(proceedings))

    df = proceedings[~proceedings["trial_status"].isin(PENDING_STATUSES)].copy()
    logger.info("After dropping pending: %d", len(df))

    terminating = _identify_terminating_fwd(decisions)
    df = df.merge(terminating, on="trial_number", how="left")

    # Nullable Int64 so unresolved rows stay distinguishable from real
    # zeros until the final dropna.
    df["cancelled"] = pd.array([pd.NA] * len(df), dtype="Int64")

    df.loc[df["trial_status"].isin(NON_FWD_LABEL_0_STATUSES), "cancelled"] = 0
    df.loc[df["trial_status"].isin(NON_FWD_LABEL_1_STATUSES), "cancelled"] = 1
    n_status = int(df["cancelled"].notna().sum())

    if "document_title" in df.columns:
        title_label = df["document_title"].fillna("").map(extract_outcome)
        needs_title = df["cancelled"].isna() & title_label.notna()
        df.loc[needs_title, "cancelled"] = title_label[needs_title].astype("Int64")
        n_title = int(needs_title.sum())
    else:
        n_title = 0

    n_text_resolved = 0
    n_text_attempted = 0
    if storage is not None and "document_identifier" in df.columns:
        unresolved = df.index[df["cancelled"].isna() & df["document_identifier"].notna()]
        n_text_attempted = len(unresolved)
        for idx in unresolved:
            doc_id = str(df.at[idx, "document_identifier"]).strip()
            if not doc_id:
                continue
            payload = storage.load_blob(Stage.DECISION_TEXTS.value, doc_id, "txt")
            if payload is None:
                continue
            label = extract_outcome(payload.decode("utf-8", errors="replace"))
            if label is not None:
                df.at[idx, "cancelled"] = label
                n_text_resolved += 1

    n_total = len(df)
    n_unresolved = int(df["cancelled"].isna().sum())
    df = df.dropna(subset=["cancelled"]).copy()
    df["cancelled"] = df["cancelled"].astype(int)

    logger.info(
        "Label resolution — status: %d, title: %d, text: %d/%d cached. "
        "Dropped %d unresolved (no FWD on file or no cached text).",
        n_status, n_title, n_text_resolved, n_text_attempted, n_unresolved,
    )
    logger.info(
        "Final dataset: %d rows (%.1f%% cancelled)",
        len(df),
        df["cancelled"].mean() * 100 if len(df) else 0.0,
    )
    if n_total and n_unresolved / n_total > 0.5:
        logger.warning(
            "More than half of post-filter trials lack a resolvable label "
            "(%d / %d). Run `drivers/run_ingest_decision_texts.py` to expand "
            "the cached FWD-text set if you need a larger training corpus.",
            n_unresolved, n_total,
        )
    return df


__all__ = ["extract_outcome", "build_labels"]
