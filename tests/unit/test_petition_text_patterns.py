"""Sanity tests for the petition-text regex constants in `parse.schemas.patterns`.

These guard against accidental breakage when the patterns are edited —
each constant should be a compiled regex (or tuple thereof) of the
expected shape, with the threshold being a positive int.
"""

import re

from ml_uspto.parse.schemas.patterns import (
    PETITION_FINTIV_FACTOR_COLON_PATTERN,
    PETITION_FINTIV_FACTOR_HEADING_PATTERN,
    PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS,
    PETITION_FINTIV_FACTOR_ORDINAL_PATTERN,
    PETITION_FINTIV_FACTOR_THRESHOLD,
    PETITION_GROUND_HEADER_PATTERN,
    PETITION_GROUND_STATUTE_102_PATTERN,
    PETITION_GROUND_STATUTE_103_PATTERN,
    PETITION_SOTERA_PATTERNS,
)


def test_scalar_patterns_are_compiled_regexes():
    for pat in (
        PETITION_GROUND_HEADER_PATTERN,
        PETITION_GROUND_STATUTE_102_PATTERN,
        PETITION_GROUND_STATUTE_103_PATTERN,
        PETITION_FINTIV_FACTOR_COLON_PATTERN,
        PETITION_FINTIV_FACTOR_ORDINAL_PATTERN,
        PETITION_FINTIV_FACTOR_HEADING_PATTERN,
    ):
        assert isinstance(pat, re.Pattern)


def test_tuple_patterns_are_compiled_regexes():
    for tup in (PETITION_SOTERA_PATTERNS, PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS):
        assert isinstance(tup, tuple)
        assert len(tup) > 0
        assert all(isinstance(p, re.Pattern) for p in tup)


def test_fintiv_threshold_is_sane():
    """Threshold must be an int between 1 and 6 (the 6 Fintiv factors)."""
    assert isinstance(PETITION_FINTIV_FACTOR_THRESHOLD, int)
    assert 1 <= PETITION_FINTIV_FACTOR_THRESHOLD <= len(
        PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS
    )


def test_ground_header_pattern_captures_digit():
    """The ground-header regex must capture the ground index in group(1)."""
    matches = [m.group(1) for m in PETITION_GROUND_HEADER_PATTERN.finditer(
        "Ground 1 ... and as Ground 2 establishes ..."
    )]
    assert matches == ["1", "2"]


def test_ground_header_rejects_us_code_column():
    """'Ground 35 U.S.C. § Claims' is a grounds-table column header, not Ground 35.

    Without the trailing `\\b` after the digit + optional letter, the
    regex would backtrack from "35" to "3" and accept it as Ground 3.
    """
    matches = list(PETITION_GROUND_HEADER_PATTERN.finditer(
        "Ground 35 U.S.C. § Claims Basis I 103 1, 2, 3"
    ))
    assert matches == []


def test_ground_header_captures_subgrounded_petitions():
    """'Ground 1A', 'Ground 1B', 'Ground 2A' → distinct numeric prefixes {1, 2}."""
    text = "GROUND 1A: claim 1 ... GROUND 1B: claim 1 ... GROUND 2A: claim 1"
    distinct = {m.group(1) for m in PETITION_GROUND_HEADER_PATTERN.finditer(text)}
    assert distinct == {"1", "2"}


def test_fintiv_colon_pattern_captures_factor_number():
    """The colon-headed structural pattern must capture the factor index."""
    m = PETITION_FINTIV_FACTOR_COLON_PATTERN.search(
        "Factor 2: The Docket Control Order sets jury selection..."
    )
    assert m is not None
    assert m.group(1) == "2"


def test_ground_header_rejects_lowercase_circuit_ground():
    """Lowercase 'ground 357' (electrical component reference) must NOT count.

    IPR2022-00100 contains 'discloses AC power lines 320 ... and a circuit
    ground 357' — case-insensitive matching previously inflated `n_grounds`
    by one. The pattern is now case-sensitive on the word boundary.
    """
    text = "Ground 1: Claims 1-3 ... discloses AC power lines and a circuit ground 357 ..."
    matches = {m.group(1) for m in PETITION_GROUND_HEADER_PATTERN.finditer(text)}
    assert matches == {"1"}


def test_ground_header_rejects_line_wrapped_token():
    """pdfplumber line-wrap 'ground\\n56' must NOT count as Ground 56.

    IPR2026-00338's pdfplumber output paired the word 'ground' at line-end
    with the next line's '56' page number; `\\s+` previously spanned the
    newline and counted '56' as a phantom Ground 56. The pattern uses
    `[ \\t]+` (horizontal whitespace only) to require same-line proximity.
    """
    text = "...the ground\n56 ... See Ground 1 ..."
    matches = {m.group(1) for m in PETITION_GROUND_HEADER_PATTERN.finditer(text)}
    assert matches == {"1"}


def test_fintiv_ordinal_pattern_captures_six_ordinals():
    """The ordinal-narrative pattern must capture all 6 lowercase ordinals.

    IPR2022-01543 has 'first/second/third/fourth/fifth/sixth Fintiv factor'
    in narrative prose; the pattern must capture each ordinal so the
    extractor can dedupe them into 6 distinct factor indices.
    """
    text = (
        "The first Fintiv factor favors institution. "
        "The second Fintiv factor weighs in favor. "
        "The third Fintiv factor weighs strongly. "
        "The fourth Fintiv factor is neutral. "
        "The fifth Fintiv factor is neutral. "
        "The sixth Fintiv factor weighs against denial. "
    )
    captured = {m.group(1).lower() for m in PETITION_FINTIV_FACTOR_ORDINAL_PATTERN.finditer(text)}
    assert captured == {"first", "second", "third", "fourth", "fifth", "sixth"}


def test_fintiv_ordinal_pattern_requires_fintiv_anchor():
    """An ordinal alone (no 'Fintiv factor' anchor) must NOT match.

    Petitions cite many other "first ... factor" phrases in case law;
    the literal 'Fintiv factor' anchor keeps ordinal hits domain-specific.
    """
    text = "The first factor in our analysis is unrelated to discretionary denial."
    matches = list(PETITION_FINTIV_FACTOR_ORDINAL_PATTERN.finditer(text))
    assert matches == []


def test_ground_header_captures_challenge_form():
    """'Challenge #N' is the formal-enumeration alternative to 'Ground N'.

    IPR2018-01724 and IPR2019-00443 use this terminology — the False-sweep
    audit surfaced both as recall gaps that the original Ground-only
    pattern silently missed.
    """
    text = (
        "A. Challenge #1: Claims 1-4 are invalid under 35 U.S.C. § 102.\n"
        "B. Challenge #2: Claim 5 is invalid under 35 U.S.C. § 103.\n"
        "C. Challenge #3: Claims 6-7 are invalid under § 103.\n"
    )
    captured = {
        (m.group(1) or m.group(2))
        for m in PETITION_GROUND_HEADER_PATTERN.finditer(text)
    }
    assert captured == {"1", "2", "3"}


def test_statute_102_pattern_catches_dropped_section_sign():
    """'35 U.S.C. 102' (no §) and 'Section 102' must count as §102 statute refs.

    pdfplumber sometimes drops the § symbol on older OCR'd PDFs
    (IPR2021-00011: '35 U.S.C. 102(a)' in TOC); some firms write
    'Section 102' as ground-table prose (IPR2021-01369).
    """
    for text in (
        "qualifies as prior art under 35 U.S.C. 102(a)",
        "Ground 1: Claim 1 is invalid under Section 102 over Smith",
        "the petition relies on § 102(b) anticipation",
    ):
        assert PETITION_GROUND_STATUTE_102_PATTERN.search(text) is not None, text


def test_statute_103_pattern_catches_dropped_section_sign():
    """Same broadening for §103 — 'Section 103' (capital S) and bare 'U.S.C. 103' count."""
    for text in (
        "Ground 1: Claim 1 is invalid under Section 103 over the combination",
        "obviousness under 35 U.S.C. 103(a)",
        "asserted under § 103",
    ):
        assert PETITION_GROUND_STATUTE_103_PATTERN.search(text) is not None, text


def test_statute_pattern_rejects_lowercase_section_component_reference():
    """Lowercase 'section 103' is intentionally NOT counted.

    IPR2022-01124's 'signal subtracting section 103 subtracts the second
    microphone' refers to component 103 in a prior-art reference, not
    statute §103. The capital-S / U.S.C.-anchored alternatives keep
    statute counts unaffected by such component references.
    """
    text = (
        "discloses that signal subtracting section 103 subtracts the second "
        "microphone, while signal amplifying section 150 amplifies the first."
    )
    assert PETITION_GROUND_STATUTE_103_PATTERN.search(text) is None


def test_statute_pattern_no_double_count_when_both_forms_appear():
    """'35 U.S.C. § 102' must match once, not twice.

    Both the literal-§ branch and the U.S.C.-prefix branch could in
    principle fire; the engine consumes the longer alternation greedily
    so we don't inflate the count when both forms are present.
    """
    text = "asserted under 35 U.S.C. § 102 over Smith"
    matches = PETITION_GROUND_STATUTE_102_PATTERN.findall(text)
    assert len(matches) == 1


def test_statute_lowercase_section_with_grounds_anchor_fires():
    """Lowercase 'section 102 ... grounds' fires (IPR2022-01412).

    The trailing `\\bgrounds?\\b` lookahead distinguishes statute references
    from prior-art component-numbered references — petitions invariably
    introduce statute citations before the word "grounds" but never
    refer to "section N grounds" when discussing prior-art components.
    """
    text = "addressing the patentability of the claims on section 102 and 103 grounds"
    assert PETITION_GROUND_STATUTE_102_PATTERN.search(text) is not None
    assert PETITION_GROUND_STATUTE_103_PATTERN.search(text) is not None


def test_statute_lowercase_section_without_grounds_anchor_rejected():
    """Lowercase 'section 103' WITHOUT trailing 'grounds' does not fire.

    IPR2022-01124's 'signal subtracting section 103 subtracts the second
    microphone' must not fire — the lookahead anchor blocks all such
    component-reference cases.
    """
    text = "discloses that signal subtracting section 103 subtracts the second microphone"
    assert PETITION_GROUND_STATUTE_103_PATTERN.search(text) is None


def test_fintiv_heading_pattern_captures_canonical_keyword_numbered_headings():
    """'1. Stay' / '2. Trial Date' / '3. Investment' line-anchored fires.

    IPR2022-01002, IPR2023-01406, and IPR2024-01267 use bare numbered
    subsections labeled with the canonical Fintiv factor keywords
    instead of "Factor N:" syntax. The line-anchor (`(?:^|\\n)`) is
    load-bearing — without it, mid-sentence prose like "the result in
    Sand Revolution at 12. Stay was denied..." would FP.
    """
    text = (
        "IV. DISCRETIONARY CONSIDERATIONS\n"
        "1. No evidence regarding a stay\n"
        "Lorem ipsum...\n"
        "2. Parallel proceeding trial date\n"
        "Lorem ipsum...\n"
        "3. Investment in parallel proceeding\n"
    )
    captured = {m.group(1) for m in PETITION_FINTIV_FACTOR_HEADING_PATTERN.finditer(text)}
    assert captured == {"1", "2", "3"}


def test_fintiv_heading_pattern_does_not_match_mid_sentence_prose():
    """Mid-sentence references like 'at 1. Stay' do not match.

    Without the line-anchor, this is a real FP risk for case-citation
    chains. The `(?:^|\\n)` requirement enforces structural-heading-only.
    """
    text = "see Sand Revolution at 1. Stay was denied; see also 2. Trial Date analysis."
    matches = list(PETITION_FINTIV_FACTOR_HEADING_PATTERN.finditer(text))
    assert matches == []


