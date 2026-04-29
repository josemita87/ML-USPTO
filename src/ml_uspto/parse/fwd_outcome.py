"""Extract the binary `cancelled` label from an *original* IPR FWD PDF.

The USPTO API never populates `decisionData.trialOutcomeCategory` with a
granular outcome — every IPR FWD comes back as the bare string
"Final Written Decision" (verified 2026-04-29 cold-run). The granular
ruling lives on the PDF cover page in one of these phrasings:

    Determining All Challenged Claims Unpatentable          → label 1
    Determining No Challenged Claims Unpatentable           → label 0
    Determining Some Challenged Claims Unpatentable         → label 0
    Determining Challenged Claims <list> Unpatentable       → label 0
    Determining Challenged Claim(s) Unpatentable            → label 1

The list of (regex, label) patterns lives in
`config/labels.yaml::fwd_pdf_outcome.patterns` and is exposed through
`schemas.patterns.FWD_PDF_OUTCOME_PATTERNS` — this module just runs them
in order and returns the first match's label.

Per `docs/scope/prediction_scope.md` §3.1, callers must apply this only
to *original* FWDs. On-remand and rehearing variants need to be filtered
out upstream (in `parse.preprocessing._identify_terminating_fwd`); their
cover-page outcomes refer to the remanded subset, not the originally-
challenged set, and would yield wrong labels.
"""

from __future__ import annotations

from ml_uspto.schemas.constants import FWD_PDF_COVER_PAGE_SEARCH_CHARS
from ml_uspto.schemas.patterns import FWD_PDF_OUTCOME_PATTERNS


def extract_outcome(text: str) -> int | None:
    """Return the binary `cancelled` label from FWD-PDF extracted text.

    Looks at the first `FWD_PDF_COVER_PAGE_SEARCH_CHARS` of `text` and
    runs `FWD_PDF_OUTCOME_PATTERNS` in order; returns the first match's
    label. Returns `None` if no pattern matches — caller should
    quarantine (image-scan PDFs, Motion-to-Amend rulings without a
    "Determining" cover line, malformed text).
    """
    head = text[:FWD_PDF_COVER_PAGE_SEARCH_CHARS]
    for entry in FWD_PDF_OUTCOME_PATTERNS:
        if entry.pattern.search(head) is not None:
            return entry.label
    return None


__all__ = ["extract_outcome"]
