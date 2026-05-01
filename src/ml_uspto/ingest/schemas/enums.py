"""Ingest-local enums."""

from enum import StrEnum


class Stage(StrEnum):
    """Cache buckets — one per fetch surface."""

    PROCEEDINGS = "proceedings"
    DOCUMENTS_PETITION_SCAN = "documents_petition_scan"
    DECISIONS = "decisions"
    DECISION_TEXTS = "decision_texts"
    PATENTS = "patents"


class FwdPdfCandidateColumn(StrEnum):
    """Columns of the FWD-PDF gap-detector output frame.

    `ingest.decisions.enumerate_missing_fwd_pdfs` produces this frame;
    `ingest.fetch.fetch_decision_pdfs` consumes it. Iterating the enum
    yields the canonical column order — pass
    `[c.value for c in FwdPdfCandidateColumn]` to the `pd.DataFrame`
    constructor when materializing.
    """

    TRIAL_NUMBER = "trial_number"
    DOCUMENT_IDENTIFIER = "document_identifier"
    FILE_DOWNLOAD_URI = "file_download_uri"
    DOCUMENT_TITLE = "document_title"
    DECISION_ISSUE_DATE = "decision_issue_date"
