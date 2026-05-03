"""Project-wide compiled regex patterns for FWD-outcome label resolution."""

import re

from ml_uspto.schemas.constants import LEGACY_FWD_AMENDMENT_TITLE_MARKERS
from ml_uspto.schemas.models import FwdOutcomePattern


# ---------------------------------------------------------------------------
# Modern FWD cover-page outcome patterns (2018+)
#
# Per `docs/scope/prediction_scope.md` §3.1 we apply these only to original
# FWDs; on-remand and rehearing variants are dropped upstream because
# their cover-page outcome refers to the remanded subset, not the
# originally-challenged set, and produces wrong labels in isolation.
#
# Empirically validated against 200 sampled FWD PDFs (2021–2026), of
# which 133 are originals — see `drivers/probe_fwd_pdfs_sample.py`. After
# the originals-only filter, 132/133 (99.2%) resolve to a label; 1
# correctly returns None (Motion-to-Amend ruling without a "Determining"
# line) so the joiner drops it instead of mislabeling.
#
# The matcher is a tuple of `(regex, label)` entries evaluated in order;
# first match wins. The order encodes the "Challenged" ambiguity: an
# unqualified "Challenged Claim(s) Unpatentable" means "all challenged
# claims unpatentable" (label 1), while an enumerated "Challenged Claims
# 1, 3–11, … Unpatentable" means only those specific claims (label 0).
# The two cases are distinguished purely by whether digits follow
# "Claim/Claims" before "Unpatentable", so the enumerated-subset pattern
# MUST appear before the implicit-all fallback.
#
# `FWD_PDF_COVER_PAGE_SEARCH_CHARS` (in `schemas.constants`) caps the
# search window so a stray "Determining" elsewhere in the body can't
# override the cover page.
FWD_PDF_OUTCOME_PATTERNS: tuple[FwdOutcomePattern, ...] = (
    # Canonical quantified forms. The word "Challenged" is optional —
    # ~3% of original FWDs in our 200-PDF sample omit it (e.g.,
    # `170536777`, `170979620`, `170182053` write "Determining All Claims
    # Unpatentable"; `170620469` writes "Determining Some Claims
    # Unpatentable"). Together these forms account for >99% of label
    # resolution on originals.
    FwdOutcomePattern(
        name="all_quantified",
        pattern=re.compile(
            r"Determining\s+All\s+(?:Challenged\s+)?Claims?\s+Unpatentable",
            re.IGNORECASE | re.DOTALL,
        ),
        label=1,
    ),
    FwdOutcomePattern(
        name="no_quantified",
        pattern=re.compile(
            r"Determining\s+No\s+(?:Challenged\s+)?Claims?\s+Unpatentable",
            re.IGNORECASE | re.DOTALL,
        ),
        label=0,
    ),
    FwdOutcomePattern(
        name="some_quantified",
        pattern=re.compile(
            r"Determining\s+Some\s+(?:Challenged\s+)?Claims?\s+Unpatentable",
            re.IGNORECASE | re.DOTALL,
        ),
        label=0,
    ),
    # Enumerated subset: "Determining Challenged Claims 1, 3–11, … Unpatentable".
    # By PTAB convention this listing form is only used when the set is a
    # strict subset of the originally-challenged claims (when it's the
    # full set the panel writes "All Challenged Claims" instead). The
    # leading digit pins the discriminator: any character class entry
    # between "Claims" and "Unpatentable" that starts with a number means
    # there's a list, so the unpatentable set is enumerated.
    FwdOutcomePattern(
        name="challenged_enumerated_subset",
        pattern=re.compile(
            r"Determining\s+Challenged\s+Claims?\s+\d[\w\s,\-–]*Unpatentable",
            re.IGNORECASE | re.DOTALL,
        ),
        label=0,
    ),
    # Unquantified, no list — implicit "all challenged claims" form.
    # Empirically rare in originals (~0.75% of sample) but real:
    # `170357947` (2023) reads "Determining Challenged Claim Unpatentable".
    # MUST be ordered after the enumerated-subset pattern so a list
    # doesn't fall through to this default.
    FwdOutcomePattern(
        name="challenged_implicit_all",
        pattern=re.compile(
            r"Determining\s+Challenged\s+Claims?\s+Unpatentable",
            re.IGNORECASE | re.DOTALL,
        ),
        label=1,
    ),
)


# ---------------------------------------------------------------------------
# Legacy ORDER-section patterns (typically 2012-2018, document_type =
# "Final Decision")
#
# The granular ruling lives at the end of the opinion in a "IV. ORDER" /
# "V. ORDER" block, e.g.:
#
#   IV. ORDER
#   Accordingly, it is:
#   ORDERED that claims 1–24 of the '162 patent have been shown to be
#   unpatentable;
#   FURTHER ORDERED that claims 25–30 ... have not been shown to be
#   unpatentable; ...
#
# Patterns anchor on `ORDERED that` to stay within the judgment block;
# the `[^;]{0,500}` window pins the match to a single semicolon-delimited
# ORDERED clause so a positive ruling in one clause and a negative
# ruling in the next don't collide.
#
# Patterns are evaluated in order: the negation form fires first because
# mixed outcomes (some claims unpatentable, others not) must resolve to
# label 0 per `docs/scope/prediction_scope.md` §3.
#
# `LEGACY_FWD_ORDER_SEARCH_CHARS` (in `schemas.constants`) clips the
# search to the last N chars of the document so body-text discussion of
# prior art ("the panel found that claim X has been shown to be
# unpatentable" inside §IV.A analysis) can't override the actual ORDER
# ruling.
LEGACY_FWD_ORDER_PATTERNS: tuple[FwdOutcomePattern, ...] = (
    # Any "not" + "unpatentable" combo inside an ORDERED clause →
    # label 0 (some claims survived). Catches all observed phrasings:
    #   "have not been shown to be unpatentable"  (passive, dominant)
    #   "has not shown ... claims X are unpatentable"  (active, e.g. IPR2018-00393)
    #   "not been determined to be unpatentable"
    #
    # MUST be ordered before the positive pattern: in a mixed outcome
    # (some claims unpatentable, some not), label 0 wins per
    # `docs/scope/prediction_scope.md` §3.
    FwdOutcomePattern(
        name="legacy_claims_survived",
        pattern=re.compile(
            r"ORDERED\s+that[^;]{0,500}\bnot\b[^;]{0,500}unpatentable",
            re.IGNORECASE | re.DOTALL,
        ),
        label=0,
    ),
    # Any "unpatentable" inside an ORDERED clause that wasn't already
    # caught by the negation form → label 1 (all challenged claims
    # unpatentable). Motion clauses ("ORDERED that Petitioner's Motion
    # to Exclude is denied") don't contain "unpatentable" so they
    # can't trigger this.
    FwdOutcomePattern(
        name="legacy_claims_unpatentable",
        pattern=re.compile(
            r"ORDERED\s+that[^;]{0,500}unpatentable",
            re.IGNORECASE | re.DOTALL,
        ),
        label=1,
    ),
)


LEGACY_FWD_AMENDMENT_PATTERN: re.Pattern[str] = re.compile(
    "|".join(re.escape(m) for m in LEGACY_FWD_AMENDMENT_TITLE_MARKERS),
    re.IGNORECASE,
)


__all__ = [
    "FWD_PDF_OUTCOME_PATTERNS",
    "LEGACY_FWD_ORDER_PATTERNS",
    "LEGACY_FWD_AMENDMENT_PATTERN",
]
