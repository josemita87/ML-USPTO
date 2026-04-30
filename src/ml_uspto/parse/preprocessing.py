"""Build the binary `cancelled` label per `docs/scope/prediction_scope.md` §3.

Target = 1 iff the trial's *original* Final Written Decision held all
challenged claims unpatentable. Everything else (institution denied,
discretionary denial, settled, procedurally terminated, FWD where any claim
survived) is 0. Trials still pending are excluded entirely.

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
     ("Determining All Challenged Claims Unpatentable …"). Apply
     `parse.fwd_outcome.extract_outcome` to the title.
  3. **PDF cover-page fallback.** For trials still unresolved, load the
     cached FWD PDF (`Stage.DECISION_PDFS / <document_identifier>.pdf`),
     extract page 0 via pdfplumber, and run the same regex. Skipped
     silently when `storage=None` (unit-test mode) or when the PDF isn't
     cached. Backfilling the remaining PDFs requires
     `drivers/probe_fwd_pdfs_sample.py` (or a successor) to widen the
     download set — at the current 200-PDF sample only ~5-10% of
     unresolved-by-title trials have a cached PDF.

The empirical sentinel layer (matching `decisionData.trialOutcomeCategory`
against `ALL_CLAIMS_UNPATENTABLE_OUTCOMES`) was removed in 2026-04 after
zero matches in the 2,180-row cold-run; if USPTO ever populates that
field, re-add it before layer 2 with one frozenset lookup.

Trials whose label is still unresolved after all layers are dropped from
the returned frame, alongside trials missing T₀ or `patent_number`. The
caller (`parse.joiner`) gets a fully-labeled `cancelled` int column.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from ml_uspto.clients.storage import Storage
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.fwd_outcome import extract_outcome
from ml_uspto.schemas.constants import (
    FWD_DECISION_TYPE_MARKER,
    FWD_ORIGINAL_MARKER,
    NON_FWD_LABEL_0_STATUSES,
    NON_FWD_LABEL_1_STATUSES,
    PENDING_STATUSES,
)
from ml_uspto.schemas.enums import TrialType

logger = logging.getLogger(__name__)


def _identify_terminating_fwd(decisions: pd.DataFrame) -> pd.DataFrame:
    """Return one row per trial with the terminating-FWD metadata needed
    for label resolution: `terminating_outcome`, `document_title`, and
    `document_identifier` (for PDF cache lookup).

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
    # NaN — the PDF-fallback layer just no-ops on those rows.
    for col in ("document_title", "document_identifier"):
        if col not in terminating.columns:
            terminating[col] = pd.NA
    return terminating[out_cols]


def preprocess(
    proceedings: pd.DataFrame,
    decisions: pd.DataFrame,
    storage: Storage | None = None,
) -> pd.DataFrame:
    """Join proceedings + decisions, derive the binary `cancelled` label.

    Returns one row per labeled trial (rows whose label couldn't be
    resolved are dropped). When `storage` is given, the PDF-cover-page
    fallback runs against `Stage.DECISION_PDFS`; when None, only
    title-based extraction is attempted.
    """
    logger.info("Raw proceedings: %d", len(proceedings))

    df = proceedings[proceedings["trial_type"] == TrialType.IPR].copy()
    logger.info("After filtering to IPR: %d", len(df))

    df = df[~df["trial_status"].isin(PENDING_STATUSES)].copy()
    logger.info("After dropping pending: %d", len(df))

    date_cols = [
        "petition_filing_date",
        "accorded_filing_date",
        "institution_decision_date",
        "grant_date",
        "latest_decision_date",
        "termination_date",
    ]
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    terminating = _identify_terminating_fwd(decisions)
    df = df.merge(terminating, on="trial_number", how="left")

    # Nullable Int64 so unresolved rows stay distinguishable from real
    # zeros until the final dropna — the previous code path silently
    # zero-filled every unresolved trial, contaminating the negative class.
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

    n_pdf_resolved = 0
    n_pdf_attempted = 0
    if storage is not None and "document_identifier" in df.columns:
        import pdfplumber  # noqa: PLC0415 — heavy; only loaded when PDF fallback runs

        unresolved = df.index[df["cancelled"].isna() & df["document_identifier"].notna()]
        n_pdf_attempted = len(unresolved)
        for idx in unresolved:
            doc_id = str(df.at[idx, "document_identifier"]).strip()
            if not doc_id:
                continue
            payload = storage.load_blob(Stage.DECISION_PDFS.value, doc_id, "pdf")
            if payload is None:
                continue
            # extract_outcome only inspects the first FWD_PDF_COVER_PAGE_SEARCH_CHARS
            # of the PDF text, so reading page 0 alone is sufficient for the regex
            # patterns. Skipping the remaining ~50 pages saves ~50× per PDF.
            try:
                with pdfplumber.open(io.BytesIO(payload)) as pdf:
                    text = pdf.pages[0].extract_text() or "" if pdf.pages else ""
            except Exception as exc:  # noqa: BLE001 — pdfplumber raises a zoo of types
                logger.warning("pdfplumber failed on %s: %s", doc_id, exc)
                continue
            label = extract_outcome(text)
            if label is not None:
                df.at[idx, "cancelled"] = label
                n_pdf_resolved += 1

    df = df.dropna(subset=["petition_filing_date", "patent_number"])

    n_unresolved = int(df["cancelled"].isna().sum())
    n_total = len(df)
    df = df.dropna(subset=["cancelled"]).copy()
    df["cancelled"] = df["cancelled"].astype(int)
    df["technology_center"] = df["technology_center"].astype(str).str.strip()

    logger.info(
        "Label resolution — status: %d, title: %d, pdf: %d/%d cached. "
        "Dropped %d unresolved (no FWD on file or no cached PDF).",
        n_status, n_title, n_pdf_resolved, n_pdf_attempted, n_unresolved,
    )
    logger.info(
        "Final dataset: %d rows (%.1f%% cancelled)",
        len(df),
        df["cancelled"].mean() * 100 if len(df) else 0.0,
    )
    if n_total and n_unresolved / n_total > 0.5:
        logger.warning(
            "More than half of post-filter trials lack a resolvable label "
            "(%d / %d). Run the PDF-backfill driver to expand the cached "
            "FWD-PDF set if you need a larger training corpus.",
            n_unresolved, n_total,
        )
    return df
