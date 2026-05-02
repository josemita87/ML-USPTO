"""Build the binary `cancelled` label per `docs/scope/prediction_scope.md` §3."""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.protocols.storage import Storage
from ml_uspto.schemas.constants import (
    FWD_ORIGINAL_DOCUMENT_TYPES,
    FWD_PDF_COVER_PAGE_SEARCH_CHARS,
    LEGACY_FWD_DOCUMENT_TYPE,
    LEGACY_FWD_ORDER_SEARCH_CHARS,
    NON_FWD_LABEL_0_STATUSES,
    NON_FWD_LABEL_1_STATUSES,
    PENDING_STATUSES,
    normalize_doctype,
)
from ml_uspto.schemas.patterns import (
    FWD_PDF_OUTCOME_PATTERNS,
    LEGACY_FWD_AMENDMENT_PATTERN,
    LEGACY_FWD_ORDER_PATTERNS,
)

logger = logging.getLogger(__name__)


def extract_outcome(text: str) -> int | None:
    """Return the binary `cancelled` label from FWD title or opinion text.

    Two-window cascade:

      1. Cover page (head, `FWD_PDF_COVER_PAGE_SEARCH_CHARS`) — modern
         FWDs encode the granular outcome on the cover via the
         "Determining ... Unpatentable" idiom; `FWD_PDF_OUTCOME_PATTERNS`
         covers the variants. The head-window cap is a correctness
         guard against stray "Determining …" citations in body text.
      2. ORDER section (tail, `LEGACY_FWD_ORDER_SEARCH_CHARS`) — legacy
         FWDs (typically pre-2018, document_type=`Final Decision`) put
         the ruling in the closing "IV. ORDER" / "V. ORDER" block;
         `LEGACY_FWD_ORDER_PATTERNS` matches "ORDERED that claims X
         have (not) been shown to be unpatentable / are determined to
         be unpatentable". The tail-window cap keeps the patterns from
         catching prior-art-discussion phrasings in the opinion body.

    Args:
        text: FWD title text or full opinion text.

    Returns:
        First matching pattern's label (cover-page checked first, then
        ORDER section), or `None` when no pattern hits (image-scan PDFs,
        Motion-to-Amend rulings, malformed text). Caller chooses whether
        to drop, quarantine, or fall back.
    """
    head = text[:FWD_PDF_COVER_PAGE_SEARCH_CHARS]
    for entry in FWD_PDF_OUTCOME_PATTERNS:
        if entry.pattern.search(head) is not None:
            return entry.label
    tail = text[-LEGACY_FWD_ORDER_SEARCH_CHARS:]
    for entry in LEGACY_FWD_ORDER_PATTERNS:
        if entry.pattern.search(tail) is not None:
            return entry.label
    return None


def _identify_terminating_fwd(decisions: pd.DataFrame) -> pd.DataFrame:
    """Return one row per trial with terminating-FWD metadata.

    Filters to *original* FWDs only across PTAB's two `document_type`
    taxonomies: modern `Final Written Decision:  original` (2018+) and
    legacy `Final Decision` (2012-2020). On-remand, rehearing, and
    supplemental variants are dropped per
    `docs/scope/prediction_scope.md` §3.1 (their cover-page outcomes
    refer to the remanded/rehearing subset, not the originally-challenged
    set). Modern data disambiguates via `document_type`; legacy data
    has a single document_type and the variant lives in the title, so
    legacy rows whose title matches `LEGACY_FWD_AMENDMENT_TITLE_MARKERS`
    are dropped before the per-trial idxmin.

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

    doc_type_norm = decisions["document_type"].fillna("").map(normalize_doctype)
    is_fwd_original = doc_type_norm.isin(FWD_ORIGINAL_DOCUMENT_TYPES)
    if "document_title" in decisions.columns:
        title_norm = decisions["document_title"].fillna("")
    else:
        title_norm = pd.Series("", index=decisions.index)
    legacy_amendment = (doc_type_norm == LEGACY_FWD_DOCUMENT_TYPE) & title_norm.str.contains(
        LEGACY_FWD_AMENDMENT_PATTERN, regex=True, na=False
    )
    fwds = decisions[is_fwd_original & ~legacy_amendment].copy()
    n_legacy_dropped = int(legacy_amendment.sum())
    if n_legacy_dropped:
        logger.info(
            "Dropped %d legacy 'Final Decision' rows whose title flagged them as "
            "amendment FWDs (on-remand / rehearing / supplemental) per §3.1",
            n_legacy_dropped,
        )
    if fwds.empty:
        return empty

    fwds["decision_issue_date"] = pd.to_datetime(
        fwds["decision_issue_date"], errors="coerce"
    )
    # Pick the EARLIEST surviving FWD per trial. The filter above should
    # already leave only originals, but if `LEGACY_FWD_AMENDMENT_TITLE_MARKERS`
    # misses an amendment phrasing we haven't catalogued, the amendment
    # is — by definition — issued *after* the original, so idxmin still
    # picks the original. This mirrors `parse.petitions.pick_petition`'s
    # `min(documentNumber)` rule (see §3.1 of prediction_scope.md): the
    # tiebreak directly encodes "originality" rather than relying on the
    # filter being exhaustive.
    idx = fwds.groupby("trial_number")["decision_issue_date"].idxmin()
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
    storage: Storage,
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
         at fetch-time from the PDF) and run the same regex. Trials
         whose text isn't cached are dropped as unresolvable.

    Args:
        proceedings: Flattened proceedings frame from `Frame.TRIALS`.
        decisions: Flattened decisions frame from `Frame.DECISIONS`.
        storage: Backend for the cached FWD-text fallback.

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

    # ── Layer 3: cached FWD-text fallback.
    n_text_resolved = 0
    n_text_attempted = 0
    if "document_identifier" in df.columns:
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
