"""Parse-local enums.

Used to keep callers off of free-string Literals: `flatten(records, Parser.PROCEEDINGS)`
instead of `flatten(records, "proceedings")`.
"""

from enum import StrEnum


class Parser(StrEnum):
    """Top-level keys in `config/parsers/patents.yaml`."""

    PROCEEDINGS = "proceedings"
    DECISIONS = "decisions"
    DOCUMENTS = "documents"
    PATENTS = "patents"


class ParserTransform(StrEnum):
    """Generic transforms supported by declarative parser column specs."""

    COUNT = "count"
    UNIQUE_LIST = "unique_list"
    YN_BOOL = "yn_bool"


class FwdPdfCandidateColumn(StrEnum):
    """Columns of the gap-detector output frame.

    `parse.decisions.enumerate_missing_fwd_pdfs` produces this frame;
    the fetch driver consumes it. Iterating the enum yields the canonical
    column order — pass `[c.value for c in FwdPdfCandidateColumn]` to the
    `pd.DataFrame` constructor when materializing.
    """

    TRIAL_NUMBER = "trial_number"
    DOCUMENT_IDENTIFIER = "document_identifier"
    FILE_DOWNLOAD_URI = "file_download_uri"
    DOCUMENT_TITLE = "document_title"
    DECISION_ISSUE_DATE = "decision_issue_date"
