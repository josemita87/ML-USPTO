"""Project-wide constants, sourced from `config/labels.yaml`."""

from functools import lru_cache

import yaml

from ml_uspto import paths


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(paths.LABELS_YAML) as f:
        return yaml.safe_load(f)


_labels = _load()

NON_FWD_LABEL_1_STATUSES: frozenset[str] = frozenset(_labels["non_fwd_label_1_statuses"])
NON_FWD_LABEL_0_STATUSES: frozenset[str] = frozenset(_labels["non_fwd_label_0_statuses"])
PENDING_STATUSES: frozenset[str] = frozenset(_labels["pending_statuses"])


def normalize_doctype(s: str) -> str:
    """Lowercase, strip, and collapse internal whitespace.

    PTAB's `documentTypeDescriptionText` drifts on whitespace (e.g.
    `Final Written Decision: original` vs `Final Written Decision:  original`,
    sometimes `Final written decision: ...`); normalizing once at compare
    time avoids YAML duplication.
    """
    return " ".join(s.lower().split())


FWD_ORIGINAL_DOCUMENT_TYPES: frozenset[str] = frozenset(
    normalize_doctype(s) for s in _labels["fwd_original_document_types"]
)
LEGACY_FWD_AMENDMENT_TITLE_MARKERS: tuple[str, ...] = tuple(
    s.lower() for s in _labels["legacy_fwd_amendment_title_markers"]
)
LEGACY_FWD_DOCUMENT_TYPE: str = normalize_doctype("Final Decision")
ALL_CLAIMS_UNPATENTABLE_OUTCOMES: frozenset[str] = frozenset(
    _labels["all_claims_unpatentable_outcomes"]
)

FWD_PDF_COVER_PAGE_SEARCH_CHARS: int = int(_labels["fwd_pdf_outcome"]["cover_page_search_chars"])
LEGACY_FWD_ORDER_SEARCH_CHARS: int = int(_labels["fwd_pdf_outcome_legacy"]["tail_search_chars"])

# boto3 ClientError codes that signal "key does not exist" across S3 ops.
# Mechanical AWS-API mapping — not domain-revisable, so it's a Python
# literal here rather than a YAML round-trip.
S3_NOT_FOUND_CODES: frozenset[str] = frozenset({"NoSuchKey", "404", "NotFound"})
