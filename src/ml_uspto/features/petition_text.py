"""Tier A petition-text features + the upstream usability filter.

Pure per-row regex extraction over the `petition_text` column the
joiner attaches from `Frame.PETITION_TEXTS`. Mirrors the patent
aggregator: row-local, no I/O, no cross-row corpus statistics.

`select_usable_rows` is the petition-text length filter that drops
trials whose extracted body is too short for meaningful regex
extraction (cache miss, ingest BLANK sentinel, all-whitespace,
cover-page-only partial). It lives alongside the aggregator because
the threshold is a property of the regex extractor, not a driver
concern — `build_features` calls both as a unit.
"""

from __future__ import annotations

import logging

import pandas as pd

from ml_uspto.features.schemas.constants import MIN_PETITION_TEXT_CHARS
from ml_uspto.parse.schemas.patterns import (
    FINTIV_FACTOR_WORD_TO_INDEX,
    PETITION_FINTIV_FACTOR_ANCHORED_DIGIT_PATTERN,
    PETITION_FINTIV_FACTOR_COLON_PATTERN,
    PETITION_FINTIV_FACTOR_HEADING_PATTERN,
    PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS,
    PETITION_FINTIV_FACTOR_LINE_PATTERN,
    PETITION_FINTIV_FACTOR_ORDINAL_PATTERN,
    PETITION_FINTIV_FACTOR_THRESHOLD,
    PETITION_FINTIV_FACTOR_WORDNUM_PATTERN,
    PETITION_GROUND_HEADER_PATTERN,
    PETITION_GROUND_STATUTE_102_PATTERN,
    PETITION_GROUND_STATUTE_103_PATTERN,
    PETITION_SOTERA_PATTERNS,
)

logger = logging.getLogger(__name__)


def select_usable_rows(joined: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose `petition_text` is too short for Tier A regex extraction.

    Internal to `build_features` — the petition-text length threshold
    is a feature-pipeline implementation detail, not a driver concern.

    `aggregate_petition_text_row` requires a real petition body to
    produce meaningful counts. Upstream sources of unusable text:
      - cache miss (no row in `Frame.PETITION_TEXTS` for this trial);
      - ingest-driver "BLANK" sentinels for scanned-image PDFs that
        pdfplumber can't decode;
      - all-whitespace pdfplumber output;
      - cover-page-only partial extractions (DocuSign envelope, caption
        page) where the body never reached the parser.
    All of these collapse to `len(text) < MIN_PETITION_TEXT_CHARS`. Real
    petitions sit two orders of magnitude above the cut (p01 ≈ 59K chars
    on the 6,366-trial cohort). Dropping these rows is the project's
    documented policy (`docs/engineering/features/features_csv_dictionary.md` §7) —
    preferred over zero-filling because zero-filled rows masquerade as
    "petition raised 0 grounds, no Sotera, no Fintiv," which the model
    would learn as a pattern correlated with the ingest-failure
    subpopulation.

    Args:
        joined: Joined frame from `parse.joiner.join_all`. Must carry
            a `petition_text` column.

    Returns:
        A copy of `joined` filtered to rows whose `petition_text` is a
        string of at least `MIN_PETITION_TEXT_CHARS` characters.
    """
    if "petition_text" not in joined.columns:
        # No-op when the column is absent — matches `build_features`'s
        # conditional Tier A path (a fixture or audit caller may pass
        # a petition-text-free frame deliberately).
        return joined
    text = joined["petition_text"]
    str_mask = text.apply(lambda v: isinstance(v, str))
    long_enough = str_mask & (text.fillna("").str.len() >= MIN_PETITION_TEXT_CHARS)
    n_dropped = int((~long_enough).sum())
    if n_dropped:
        logger.info(
            "Dropped %d/%d trials (%.2f%%) whose petition_text < %d chars "
            "(no cache hit, ingest BLANK sentinel, or extraction failure)",
            n_dropped, len(joined), n_dropped / max(len(joined), 1) * 100,
            MIN_PETITION_TEXT_CHARS,
        )
    return joined.loc[long_enough].copy()


def aggregate_petition_text_row(text: str) -> dict[str, int]:
    """Run the Tier A regex set over one petition's pdfplumber-extracted text.

    Mirrors `_aggregate_patent_row`: row-local, no I/O. Callers must
    run `select_usable_rows` upstream — this aggregator assumes `text`
    is a real petition body (no missing/empty/short-text branch).
    Booleans are cast to 0/1 so the result drops straight into a
    numeric DataFrame column.
    """
    # Four capture groups across three branches: group(1) is the
    # "Ground N" digit, group(2) is the "Challenge #N" digit, groups
    # (3) and (4) are both digits from a "Grounds N and M" plural-form
    # match. Each match contributes 1-2 digits to the distinct set.
    digits: set[str] = set()
    for m in PETITION_GROUND_HEADER_PATTERN.finditer(text):
        for g in m.groups():
            if g:
                digits.add(g)
    n_grounds = len(digits)

    # Fintiv: a petition fires if ANY signal reaches the threshold of 3
    # distinct factor indices. Seven phrasing variants captured:
    #   (a) keyword-anchored "Factor N + canonical keyword"
    #   (b) colon/period-headed "Factor N: ..." (any phrasing)
    #   (c) ordinal narrative "the first/second/.../sixth Fintiv factor"
    #   (d) bare numbered headings "1. Stay" / "2. Trial Date" line-anchored
    #   (e) word-form "Factor one / two / .../ six" (IPR2024-00323)
    #   (f) line-anchored "Factor [1-6]" without delimiter (IPR2024-01463)
    #   (g) "Fintiv factor [1-6]" digit-anchored (IPR2025-00555 narrative)
    # Variants (e) and (f) are gated by an explicit "Fintiv" mention
    # somewhere in the petition — without that precondition, line-
    # anchored "Factor 1: ..." / "Factor one ..." in non-Fintiv contexts
    # (Graham §103 obviousness factors, KSR factors) would FP.
    n_keyword_hits = sum(
        1 for p in PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS if p.search(text) is not None
    )
    n_colon_hits = len({m.group(1) for m in PETITION_FINTIV_FACTOR_COLON_PATTERN.finditer(text)})
    n_ordinal_hits = len({
        FINTIV_FACTOR_WORD_TO_INDEX[m.group(1).lower()]
        for m in PETITION_FINTIV_FACTOR_ORDINAL_PATTERN.finditer(text)
    })
    n_heading_hits = len({m.group(1) for m in PETITION_FINTIV_FACTOR_HEADING_PATTERN.finditer(text)})
    # Three substring checks beat a full text.lower() at corpus scale —
    # petitions are 100-500KB and a lower() copy each call is wasteful
    # when only one literal needs case-insensitive matching.
    fintiv_anywhere = "Fintiv" in text or "fintiv" in text or "FINTIV" in text
    n_wordnum_hits = (
        len({
            FINTIV_FACTOR_WORD_TO_INDEX[m.group(1).lower()]
            for m in PETITION_FINTIV_FACTOR_WORDNUM_PATTERN.finditer(text)
        })
        if fintiv_anywhere else 0
    )
    n_line_hits = (
        len({m.group(1) for m in PETITION_FINTIV_FACTOR_LINE_PATTERN.finditer(text)})
        if fintiv_anywhere else 0
    )
    n_anchored_hits = len({
        d
        for m in PETITION_FINTIV_FACTOR_ANCHORED_DIGIT_PATTERN.finditer(text)
        for d in m.groups() if d
    })
    fintiv = max(
        n_keyword_hits, n_colon_hits, n_ordinal_hits, n_heading_hits,
        n_wordnum_hits, n_line_hits, n_anchored_hits,
    ) >= PETITION_FINTIV_FACTOR_THRESHOLD

    return {
        "n_grounds": n_grounds,
        "n_grounds_102": len(PETITION_GROUND_STATUTE_102_PATTERN.findall(text)),
        "n_grounds_103": len(PETITION_GROUND_STATUTE_103_PATTERN.findall(text)),
        "has_sotera_stipulation": int(
            any(p.search(text) is not None for p in PETITION_SOTERA_PATTERNS)
        ),
        "mentions_fintiv_factors": int(fintiv),
    }


__all__ = ["aggregate_petition_text_row", "select_usable_rows"]
