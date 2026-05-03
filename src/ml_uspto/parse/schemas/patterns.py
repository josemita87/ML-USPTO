"""Compiled regex patterns for `ml_uspto.parse`.

Regression anchor (IPR2022-01002): n_grounds=1, n_grounds_103=1,
has_sotera_stipulation=True, mentions_fintiv_factors=True.
"""

import re


# ---------------------------------------------------------------------------
# Petition-picker title regexes (consumed by `parse.petitions`)
#
# Real petition titles take many shapes (`Petition for IPR`,
# `[PUBLIC] Petition for IPR`, `Inter Partes Review Petition of …`,
# `IPR Petition - <party> <patent#>`, `Corrected Petition for IPR`); a
# tighter regex empirically over-rejects. False-positives within the
# PETITION+Paper bucket are caught by `BLACKLIST` below (Ranking
# Documents, post-decision petitions, etc.).
# ---------------------------------------------------------------------------

PETITION_TITLE: re.Pattern[str] = re.compile(
    r"\bpetition\b"
    r"|\binter\s+part(?:e|ie)s\s+review\s+of\b"
    r"|\brequest\s+for\s+(?:inter\s+part(?:e|ie)s\s+review|ipr)\b",
    re.I,
)

# The `petitioner['']?s\s+(?!petition\b)` clause uses a negative lookahead so
# "Petitioner's Reply" / "Petitioner's Mandatory Notices" / etc. are rejected
# while "Petitioner's Petition for Inter Partes Review" passes through.
#
# `Petitioner's Petition Ranking and Explanation of Material Differences`
# is a procedural multi-petition filing (37 CFR §42.108), NOT the
# operative petition. The `petitioner['s] (?!petition)` clause above
# doesn't catch it (next word IS `petition`), so we blacklist `petition
# ranking` explicitly.
BLACKLIST: re.Pattern[str] = re.compile(
    r"power of attorney"
    r"|notice of appeal"
    r"|petitioner['’]?s\s+(?!petition\b)"
    r"|notice of (filing date accorded|accord)"
    r"|request for (refund|rehearing)"
    r"|sur-?reply"
    r"|surreply"
    r"|response to petition"
    r"|denying institution"
    r"|institution of inter partes"
    r"|motion for joinder"
    r"|grant of motion for joinder"
    r"|petition for reconsideration"
    r"|petition for rehearing"
    r"|petition for joinder"
    r"|petition ranking",
    re.I,
)


# ---------------------------------------------------------------------------
# Petition-text Tier A patterns (consumed by `features.transforms`)
# ---------------------------------------------------------------------------

# §I Grounds table headers. Petitions enumerate "Ground 1", "Ground 2",
# etc.; some petitions split each ground into sub-grounds with letter
# suffixes ("Ground 1A", "Ground 1B"). We capture only the digit (the
# letter is consumed by `(?:[A-Z])?` but not captured), so sub-grounded
# petitions correctly count 2 (not 2 × n_letters). The trailing `\b`
# blocks backtracking — without it, "Ground 35" against the negative
# lookahead `(?!...U.?S.?C.?)` would fail on "35", backtrack to match
# `\d+` = "3", and accept "3" (since "5 U.S.C." doesn't start with
# whitespace+U). The `\b` anchors the full digit/letter token before
# the negative lookahead runs.
#
# Three precision guards (see audit cohort):
#   * Match only "Ground" (proper case) or "GROUND" (all-caps headers) —
#     reject lowercase "ground". Petitioners always title-case formal
#     ground headers; lowercase "ground N" reliably refers to electrical
#     ground in component descriptions (IPR2022-00100: "circuit ground 357").
#   * Use `[ \t]+` (horizontal whitespace) instead of `\s+` so the digit
#     must follow the word "Ground" on the same line. With `\s+` we picked
#     up pdfplumber line-wrap artifacts where "ground" at line-end was
#     paired with the next line's page number (IPR2026-00338: "ground\n56").
#   * Negative lookahead `(?!...U.?S.?C.?)` rejects table-column header
#     "Ground 35 U.S.C. § Claims..." (IPR2022-01011) — the "Ground" /
#     "35 U.S.C." cells become adjacent in pdfplumber output.
#
# Alternative formal-enumeration form: a small minority of petitions
# (IPR2018-01724, IPR2019-00443) use "Challenge #N" instead of "Ground N"
# as the formal section-heading terminology. The False-sweep audit
# surfaced both as recall gaps. The `\bChallenge\s+#?(\d+)\b` alternative
# captures this clean second form — `Challenge #N` is unambiguous in
# petitions (no FP risk; the word is rarely used in non-enumeration
# contexts).
# Plural-enumeration form "Grounds N and M" — petitioners sometimes
# combine two consecutive grounds in a shared subsection ("Grounds 1
# and 2: The Allgenesis PCT anticipates..." in IPR2020-01438). The
# singular branch above misses this because `[ \t]+` after "Ground"
# fails on "Grounds" (extra "s"). Two capture groups so the consumer
# collects both digits per match.
PETITION_GROUND_HEADER_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:GROUND|Ground)[ \t]+(\d+)(?:[A-Z])?\b(?!\s*U\.?\s*S\.?\s*C\.?)"
    r"|\bChallenge\s+#?(\d+)\b"
    r"|\b(?:GROUNDS|Grounds)[ \t]+(\d+)[ \t]+and[ \t]+(\d+)\b(?!\s*U\.?\s*S\.?\s*C\.?)"
)

# Statutory bases for grounds. Loose counting is fine — false positives
# from §VI mandatory-notices are negligible because that section uses
# §§ 42.x rules, not § 102/103 statutes. The literal section sign is the
# high-precision discriminator; "35 U.S.C." precedes many but not all
# instances.
#
# Four precision-preserving alternatives capture the same statute when
# the § symbol is dropped (False-sweep audit findings):
#   1. `§ 10[23]` — the literal section sign (canonical, most common)
#   2. `35 U.S.C. 10[23]` — pdfplumber sometimes mangles § on older
#      OCR'd PDFs (IPR2021-00011 TOC has "35 U.S.C. 102(a)" without §);
#      some firms style this way deliberately (IPR2025-00241).
#   3. `Section 10[23]` — capital-S only. Some firms use sentence-case
#      "Section 103" as ground-table prose (IPR2021-01369: "Ground 1:
#      Claim 1 is invalid under Section 103 over...").
#   4. `section 10[23] [and 10X] grounds?` — narrow lowercase form.
#      The trailing `\bgrounds?\b` is the discriminator: statute
#      references in petitions almost always introduce "grounds" within
#      a few words ("on section 102 and 103 grounds"); component-
#      numbered references in prior-art prose never do. Catches
#      IPR2022-01412 ("section 102 and 103 grounds") without FP-ing on
#      IPR2022-01124's "signal subtracting section 103 subtracts...".
#
# When both forms appear simultaneously ("35 U.S.C. § 102") the engine
# matches once at the `35\s+U\.?S\.?C\.?` prefix and consumes the entire
# token — no double-counting.
# Three additional precision-preserving alternatives extend coverage:
#   * `§{1,2}\s*102\b` (replacing `§\s*102\b`) — petitions sometimes
#     write "§§ 102/103" when citing both statutes together; the single
#     `§` form never matched because `\s*` cannot consume a second `§`.
#   * `\b10[23]\([a-z]\)` — subsection notation. "(EX1005) 102(b)" rows
#     in prior-art status tables (IPR2025-01498) have no `§` prefix
#     because the column header carries it. The `\([a-z]\)` suffix is
#     unambiguously a statute subsection — almost zero FP risk.
#   * Line-anchored grounds-table-row form: `(?m)^\s*[1-9]\d?\s+102\s+
#     \d+(?:[-,]\s*\d+)*` catches the "1 103 1-3, 5..." row style used
#     by IPR2024-00684 / IPR2025-01498 where the column header sits in
#     a separate line ("Ground 35 U.S.C. § Claim(s) Prior Art Refs")
#     so individual rows have no statute prefix tokens. The trailing
#     claim-range pattern (digit + hyphen/comma + digit) is the
#     discriminator that filters out paragraph-number footnote forms.
PETITION_GROUND_STATUTE_102_PATTERN: re.Pattern[str] = re.compile(
    r"§{1,2}\s*102\b"
    r"|\b35\s+U\.?\s*S\.?\s*C\.?\s+(?:§{1,2}\s*)?102\b"
    r"|\bSection\s+102\b"
    r"|\bsection\s+102\b(?=(?:\s+and\s+10[23])?\s+grounds?\b)"
    r"|(?<!42\.)\b102\([a-z]\)"
    r"|(?m:^\s*[1-9]\d?\s+102\s+\d+(?:[-,]\s*\d+)*)"
)
# 103 mirrors 102 plus one extra branch for the slash/ampersand-joined
# "§§ 102/103" or "§§ 102 & 103" form (IPR2024-00987) — the leading
# "102" eats the position before "103" so neither single-§ nor the
# double-§ relaxation alone catches it; the dedicated branch handles
# the bridging punctuation.
PETITION_GROUND_STATUTE_103_PATTERN: re.Pattern[str] = re.compile(
    r"§{1,2}\s*103\b"
    r"|\b35\s+U\.?\s*S\.?\s*C\.?\s+(?:§{1,2}\s*)?103\b"
    r"|\b35\s+U\.?\s*S\.?\s*C\.?\s+§{1,2}\s*\d+\s*[/&]\s*103\b"
    r"|\bSection\s+103\b"
    r"|\bsection\s+103\b(?=\s+grounds?\b)"
    r"|\bsection\s+102\s+and\s+103\b(?=\s+grounds?\b)"
    r"|(?<!42\.)\b103\([a-z]\)"
    r"|(?m:^\s*[1-9]\d?\s+103\s+\d+(?:[-,]\s*\d+)*)"
)

# §IV.4 Sotera-style stipulation — petitioner agrees not to pursue
# invalidity grounds in parallel district-court litigation (Sotera
# Wireless v. Masimo, IPR2020-01019). Verbatim phrasings vary across
# firms but converge on the same legal commitment; matching ANY
# alternative → True. Empirically these five alternatives cover the
# common stipulation phrasings; tune against fixtures.
PETITION_SOTERA_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE | re.DOTALL)
    for p in (
        # "will not [verb] [...] (invalidity|grounds)" — covers verbs
        # observed across the validation cohort (assert/pursue/advance/
        # maintain/raise/argue) and allows an insertion phrase between
        # "not" and the verb (IPR2022-00500: "will not, as to any claims...,
        # pursue"). IPR2023-01117 uses "will not argue ... invalidity".
        # The trailing window allows ANY char (including periods) because
        # PDF page-break footers like "U.S. Patent No." routinely
        # interrupt between the verb and the grounds/invalidity target.
        r"will\s+not[^.]{0,80}(?:assert|pursue|advance|maintain|raise|argue|rely|seek).{0,300}(?:invalidit|ground)",
        # "will cease asserting [...] invalidity"
        r"will\s+cease[^.]{0,80}assert\w*.{0,200}invalidit",
        # "stipulates [that it will] not [verb] [...] (invalidity|grounds)"
        # — IPR2024-00117 uses "stipulates not to pursue ... grounds"
        # (with a page-break footer in between); IPR2025-00245 uses
        # "stipulates that it will not advance ... grounds".
        r"stipulat\w+[^.]{0,200}(?:not\s+to|will\s+not)[^.]{0,80}(?:assert|pursue|advance|maintain|raise|argue|rely|seek).{0,300}(?:invalidit|ground)",
        # "agree(s) not to [verb] [...] (invalidity|grounds)"
        r"agree\w*\s+not\s+to.{0,200}(?:invalidit|ground)",
        # "stipulates [...] cease [...] invalidity" — the canonical
        # IPR2022-01002 phrasing.
        r"stipulat\w+[^.]{0,300}cease.{0,200}invalidit",
    )
)

# §IV Discretionary Considerations — Fintiv-factor section headers. Per
# Apple v. Fintiv (IPR2020-00019), petitioners that institute under
# parallel litigation address all six factors explicitly with subheadings.
# The consumer fires when ≥ `PETITION_FINTIV_FACTOR_THRESHOLD` distinct
# factor indices appear — by EITHER signal:
#
#  * `_KEYWORD_PATTERNS` (per-factor): "Factor N" + the canonical
#    keyword for that factor ("stay", "trial date", "investment",
#    "overlap", "petitioner", "other"). This is the precise signal —
#    petitioners that follow the canonical Fintiv layout always have
#    these keywords in the headed line.
#
#  * `_COLON_PATTERN` (structural): "Factor N:" or "Factor N." — the
#    colon/period delimiter is itself a strong signal of headed
#    structure even when the petitioner phrases the heading in their
#    own words ("Factor 2: The Docket Control Order..." rather than
#    the canonical "Factor 2: Trial Date"). Without this fallback
#    IPR2023-01441 misses the flag despite having all 6 headed factors.
#
# A passing mention of "Fintiv" without per-factor headed structure
# must not flip the flag — neither pattern fires for inline citations
# like "(factor 2)".
PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"Factor\s+1[^A-Za-z]{0,8}.{0,80}stay",
        r"Factor\s+2[^A-Za-z]{0,8}.{0,80}trial\s+date",
        r"Factor\s+3[^A-Za-z]{0,8}.{0,80}investment",
        r"Factor\s+4[^A-Za-z]{0,8}.{0,80}overlap",
        r"Factor\s+5[^A-Za-z]{0,8}.{0,80}petitioner",
        r"Factor\s+6[^A-Za-z]{0,8}.{0,80}other",
    )
)
# Captures the factor number from a colon/period-delimited heading.
PETITION_FINTIV_FACTOR_COLON_PATTERN: re.Pattern[str] = re.compile(
    r"\bFactor\s+([1-6])\s*[:.]", re.IGNORECASE
)

# Narrative-ordinal Fintiv references — petitioners who walk through
# the factors in prose without per-factor headings still consistently
# write "the first Fintiv factor", "the second Fintiv factor", etc.
# IPR2021-00606 is the canonical case. The literal "Fintiv factor"
# anchor keeps false-positive risk low (no other doctrine pairs an
# ordinal with that exact phrase). Captures the ordinal index 1-6.
PETITION_FINTIV_FACTOR_ORDINAL_PATTERN: re.Pattern[str] = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth)\s+Fintiv\s+factor",
    re.IGNORECASE,
)
# Both `first`/`second`/... (ordinal narrative) and `one`/`two`/...
# (cardinal "Factor one/two/..." word-form) map to the same digits;
# the two consuming patterns match disjoint vocabularies, so a single
# combined lookup keeps the digit-mapping invariant in one place.
FINTIV_FACTOR_WORD_TO_INDEX: dict[str, str] = {
    "first": "1", "second": "2", "third": "3",
    "fourth": "4", "fifth": "5", "sixth": "6",
    "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6",
}

# Canonical-keyword numbered headings — petitioners who use bare
# numbered subsections labeled with the canonical Fintiv factor names
# instead of "Factor N:" syntax. The heading pattern requires:
#   1. line-start position (after a newline) to anchor as a structural
#      heading rather than mid-sentence prose
#   2. digit 1-6 + period (the Fintiv factor index)
#   3. the canonical keyword for that factor — Stay, Trial Date,
#      Investment, Overlap, Petitioner, Other (case-insensitive)
# IPR2022-01002 is the canonical case ("1. No evidence regarding a
# stay" / "2. Parallel proceeding trial date" / ... under "IV.
# DISCRETIONARY CONSIDERATIONS"); IPR2023-01406 and IPR2024-01267
# also fire under this signal.
#
# Captures the digit so the consumer can count distinct factor
# indices the same way as the colon-headed pattern.
PETITION_FINTIV_FACTOR_HEADING_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|\n)\s*([1-6])\.\s+"
    r"(?:Stay\b|Trial[- ]Date\b|Investment\b|Overlap\w*\b|"
    r"Petitioner\b|Other\b|No\s+evidence\s+regarding\s+a\s+stay|"
    r"Parallel\s+proceeding\s+trial\s+date)",
    re.IGNORECASE,
)
# Written-out-ordinal "Factor one/two/.../six" — IPR2024-00323 walks
# all six factors as substantive paragraph leaders using the word form
# rather than digits ("Factor one appears neutral. ... Factor six
# favors institution."). The narrative-ordinal pattern above only
# catches "first/second/.../sixth Fintiv factor" (ordinal-before-noun
# form); this one catches the digit-position word form
# (noun-before-ordinal). Captures the word for digit-mapping in the
# consumer.
PETITION_FINTIV_FACTOR_WORDNUM_PATTERN: re.Pattern[str] = re.compile(
    r"\bFactor\s+(one|two|three|four|five|six)\b",
    re.IGNORECASE,
)
# Line-anchored "Factor [1-6]" without colon/period delimiter —
# IPR2024-01463 walks Factor 1-6 with bare "Factor 1 is neutral; ...
# Factor 2 is neutral based on..." per-factor sentences. The colon
# pattern above requires `Factor N:` or `Factor N.`; the keyword
# pattern requires the canonical keyword within 80 chars, which
# fails when petitioners describe factors in their own words ("Factor
# 2 is neutral based on an expected Final Written Decision (FWD) date"
# — no "trial date" anchor). Line-anchoring `(?:^|\n)\s*` discriminates
# from inline parentheticals like "(Factor 3)" (IPR2022-01412) which
# are mid-sentence prose and must NOT fire.
PETITION_FINTIV_FACTOR_LINE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:^|\n)\s*Factor\s+([1-6])\b",
    re.IGNORECASE,
)

# "Fintiv factor [1-6]" digit-anchored form — IPR2025-00555 uses
# narrative prose with the literal "Fintiv factor N" anchor before
# each digit ("Fintiv factor 4 favors institution. ... Fintiv factor 2
# also strongly favors. ... Fintiv factor 1 also favors..."). Both
# singular ("Fintiv factor 4") and plural-with-and ("Fintiv factors 3
# and 6") forms are captured — the second digit lives in group(2).
PETITION_FINTIV_FACTOR_ANCHORED_DIGIT_PATTERN: re.Pattern[str] = re.compile(
    r"\bFintiv\s+factor[s]?\s+([1-6])(?:\s+and\s+([1-6]))?\b",
    re.IGNORECASE,
)

PETITION_FINTIV_FACTOR_THRESHOLD: int = 3


__all__ = [
    "PETITION_TITLE",
    "BLACKLIST",
    "PETITION_GROUND_HEADER_PATTERN",
    "PETITION_GROUND_STATUTE_102_PATTERN",
    "PETITION_GROUND_STATUTE_103_PATTERN",
    "PETITION_SOTERA_PATTERNS",
    "PETITION_FINTIV_FACTOR_KEYWORD_PATTERNS",
    "PETITION_FINTIV_FACTOR_COLON_PATTERN",
    "PETITION_FINTIV_FACTOR_ORDINAL_PATTERN",
    "PETITION_FINTIV_FACTOR_HEADING_PATTERN",
    "PETITION_FINTIV_FACTOR_WORDNUM_PATTERN",
    "PETITION_FINTIV_FACTOR_LINE_PATTERN",
    "PETITION_FINTIV_FACTOR_ANCHORED_DIGIT_PATTERN",
    "PETITION_FINTIV_FACTOR_THRESHOLD",
]
