"""Tier A petition-text features + the upstream usability filter.

See `docs/engineering/features/features_csv_dictionary.md` §7.
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

    Cache miss / BLANK sentinel / cover-page-only / all-whitespace all
    collapse to `len(text) < MIN_PETITION_TEXT_CHARS`. Raises if the
    column is absent — Tier A is mandatory in `build_features`.
    """
    if "petition_text" not in joined.columns:
        raise KeyError(
            "select_usable_rows requires a 'petition_text' column on the "
            "joined frame; petition-text features are mandatory in build_features"
        )
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
    """Tier A regex extractor for one petition. Assumes `select_usable_rows` ran upstream."""
    # Up to 4 capture groups: "Ground N", "Challenge #N", "Grounds N and M".
    digits: set[str] = set()
    for m in PETITION_GROUND_HEADER_PATTERN.finditer(text):
        for g in m.groups():
            if g:
                digits.add(g)
    n_grounds = len(digits)

    n_keyword_hits = sum(
        1 for p in PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS if p.search(text) is not None
    )
    n_colon_hits = len({m.group(1) for m in PETITION_FINTIV_FACTOR_COLON_PATTERN.finditer(text)})
    n_ordinal_hits = len({
        FINTIV_FACTOR_WORD_TO_INDEX[m.group(1).lower()]
        for m in PETITION_FINTIV_FACTOR_ORDINAL_PATTERN.finditer(text)
    })
    n_heading_hits = len({m.group(1) for m in PETITION_FINTIV_FACTOR_HEADING_PATTERN.finditer(text)})
    # Substring checks beat text.lower() at corpus scale (100-500KB petitions).
    # Word-form / bare-line variants gate on this to avoid Graham/KSR FPs.
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
