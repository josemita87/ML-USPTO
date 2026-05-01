"""Build the binary `cancelled` label per `docs/scope/prediction_scope.md` §3."""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.protocols.storage import Storage
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

    Inspects only the first `FWD_PDF_COVER_PAGE_SEARCH_CHARS` of `text`
    and runs `FWD_PDF_OUTCOME_PATTERNS` in order. The head-window cap is
    a correctness guard — it stops a stray "Determining …" inside a
    citation deeper in the opinion from overriding the cover-page ruling.

    Args:
        text: FWD title text or full opinion text.

    Returns:
        First matching pattern's label, or `None` when no pattern hits
        (image-scan PDFs, Motion-to-Amend rulings without a "Determining"
        cover line, malformed text). Caller chooses whether to drop,
        quarantine, or fall back.
    """
    head = text[:FWD_PDF_COVER_PAGE_SEARCH_CHARS]
    for entry in FWD_PDF_OUTCOME_PATTERNS:
        if entry.pattern.search(head) is not None:
            return entry.label
    return None


def _identify_terminating_fwd(decisions: pd.DataFrame) -> pd.DataFrame:
    """Return one row per trial with terminating-FWD metadata.

    Filters to *original* FWDs only — `documentTypeDescriptionText`
    matching both `FWD_DECISION_TYPE_MARKER` and `FWD_ORIGINAL_MARKER`.
    On-remand, rehearing, and Director-remand variants are dropped per
    `docs/scope/prediction_scope.md` §3.1 (their cover-page outcomes
    refer to the remanded/rehearing subset, not the originally-challenged
    set, so they're not valid label sources).

    Args:
        decisions: Flattened decisions frame from `Frame.DECISIONS`.

    Returns:
        Frame with columns `trial_number`, `terminating_outcome`,
        `document_title`, `document_identifier` — the fields the label
        cascade and text-blob lookup need.
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

    Resolves the label in three layers, each filling rows the previous
    layer left unresolved:
      1. Status-based — `trial_status ∈ NON_FWD_LABEL_{0,1}_STATUSES`
         maps directly to 0/1 (Institution Denied, Settled, Adverse
         Judgment, …).
      2. `document_title` regex — ~41% of original FWDs encode the
         granular ruling in the API-returned title text.
      3. Cached FWD-text fallback — load
         `Stage.DECISION_TEXTS / <document_identifier>.txt` (produced
         at fetch-time from the PDF) and run the same regex. Skipped
         when `storage=None` or the text isn't cached.

    Args:
        proceedings: Flattened proceedings frame from `Frame.TRIALS`.
        decisions: Flattened decisions frame from `Frame.DECISIONS`.
        storage: Backend for the cached FWD-text fallback. When `None`,
            layers 1–2 still run; layer 3 is skipped silently.

    Returns:
        One row per labeled trial — original `proceedings` columns plus
        the merged FWD metadata (`terminating_outcome`, `document_title`,
        `document_identifier`) plus the `cancelled` int. Rows in
        `PENDING_STATUSES` and rows the cascade cannot resolve are
        dropped.
    """
    logger.info("Raw proceedings: %d", len(proceedings))

    # ── Drop pending: in-flight trials have no terminal outcome to label.
    df = proceedings[~proceedings["trial_status"].isin(PENDING_STATUSES)].copy()
    logger.info("After dropping pending: %d", len(df))

    # ── Attach terminating-FWD metadata (left-join so non-FWD trials keep their row).
    terminating = _identify_terminating_fwd(decisions)
    df = df.merge(terminating, on="trial_number", how="left")

    # ── Init cancelled as nullable Int64 so unresolved stays distinct from real 0
    # all the way to the final dropna.
    df["cancelled"] = pd.array([pd.NA] * len(df), dtype="Int64")

    # ── Layer 1: status-based — frozenset lookup against the 0/1 taxonomies.
    df.loc[df["trial_status"].isin(NON_FWD_LABEL_0_STATUSES), "cancelled"] = 0
    df.loc[df["trial_status"].isin(NON_FWD_LABEL_1_STATUSES), "cancelled"] = 1
    n_status = int(df["cancelled"].notna().sum())

    # ── Layer 2: document_title regex on rows still unresolved.
    if "document_title" in df.columns:
        title_label = df["document_title"].fillna("").map(extract_outcome)
        needs_title = df["cancelled"].isna() & title_label.notna()
        df.loc[needs_title, "cancelled"] = title_label[needs_title].astype("Int64")
        n_title = int(needs_title.sum())
    else:
        n_title = 0

    # ── Layer 3: cached FWD-text fallback (skipped without storage).
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

    # ── Drop unresolved + cast to plain int for the downstream schema.
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
