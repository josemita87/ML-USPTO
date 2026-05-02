"""Integration tests for petition-text Tier A extraction against cached PDFs.

Like `test_fwd_outcome_cached.py`, this reads from the live petition-text
cache rather than checked-in fixtures: the regex set is the load-bearing
feature-extraction logic for the petition pipeline, and the only credible
test is "does it parse real-world petitions across the year/style
spectrum". The validation cohort is curated from a stratified year-by-year
sample (2018-2026) and represents the breadth of phrasing variation we
observed during manual ground-truth derivation:

  * Sotera stipulations: "will not assert ... invalidity", "will cease
    asserting ... invalidity", "stipulates ... will not advance ... grounds",
    "stipulates not to pursue ... grounds" (with PDF page-break footers
    interrupting), "will not argue ... invalidity ... grounds", and
    "Sotera Wireless, Inc." as petitioner-name only (a real false-positive
    trap — the namesake of the doctrine). Stipulations referenced only
    via exhibit list (no body commitment language) do NOT fire.
  * Fintiv structure: full headed factor sections (3-of-6 threshold
    fires), petitioner-paraphrased headings ("Factor 2: The Docket
    Control Order..." captured by the colon-anchored fallback),
    narrative-ordinal references ("the first/second/third Fintiv
    factor" captured by the ordinal pattern — IPR2021-00606 was the
    canonical case). Inline parenthetical citations only
    ("(factor 2)") and bare numbered subsections under a Fintiv
    heading without "Factor"/"Fintiv factor" terminology do NOT fire
    by design (precision over recall — IPR2022-01002 has 6 numbered
    subsections under "IV. DISCRETIONARY CONSIDERATIONS" but no
    canonical markers, so fintiv=False).
  * Grounds enumeration: integer-only ("Ground 1"), letter-suffixed
    sub-grounds ("Ground 1A"/"1B"), grounds-table column headers
    ("Ground 35 U.S.C. §" — must NOT count as Ground 35). FP traps
    fixed in the audit: lowercase "circuit ground 357" (electrical
    component reference, IPR2022-00100) — pattern is now case-
    sensitive on the word "Ground"; pdfplumber line-wrap
    "ground\n56" (IPR2026-00338) — pattern requires horizontal
    whitespace between "Ground" and the digit.

Each trial is loaded only if its cached text blob exists; missing blobs
are skipped (so CI without the petition cache passes). When the cache
is present, every assertion must hold — any miss flags a regex
regression on a known phrasing pattern.

Word count is intentionally not validated here: §42.24 caps every IPR
petition at 14,000 words so the asserted count concentrates near the
cap and contributes little prediction signal — the field was dropped
from `PetitionTextFeatures` rather than chase regex churn over varied
cert phrasings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ml_uspto.features.transforms import (
    _aggregate_petition_text_row as extract_features,
)
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.paths import raw_dir

# Manually-audited ground truth. Every value derived by reading the
# actual cached petition text — heuristic auto-derivation produced
# false positives (notably IPR2020-00967 where the petitioner is named
# "Sotera Wireless, Inc.", and IPR2024-00117 where Fintiv is briefed
# via inline parenthetical citations rather than headed factor sections).
PETITION_GROUND_TRUTH: dict[str, dict] = {
    # ── Pre-Fintiv era (no discretionary-denial framework). ────────────
    "IPR2018-00377": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2018-01097": dict(sotera=False, fintiv=False, n_grounds=0),
    # Petition uses "Challenge #N" instead of "Ground N" — captured by
    # the alternative-enumeration branch of `PETITION_GROUND_HEADER_PATTERN`.
    "IPR2018-01724": dict(sotera=False, fintiv=False, n_grounds=1),
    "IPR2019-00516": dict(sotera=False, fintiv=False, n_grounds=7),
    "IPR2019-00461": dict(sotera=False, fintiv=False, n_grounds=0),
    "IPR2019-01022": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2019-01565": dict(sotera=False, fintiv=False, n_grounds=3),
    # ── 2020: Apple v. Fintiv (IPR2020-00019) issued 2020-03-20; ───────
    #    Sotera Wireless v. Masimo (IPR2020-01019) precedential 2020-12-01.
    "IPR2020-00353": dict(sotera=False, fintiv=False, n_grounds=0),
    "IPR2020-00386": dict(sotera=False, fintiv=False, n_grounds=4),
    "IPR2020-00430": dict(sotera=False, fintiv=False, n_grounds=2),
    # Sotera-namesake trap: petitioner is "Sotera Wireless, Inc.", but no
    # actual stipulation language appears in the petition body.
    "IPR2020-00967": dict(sotera=False, fintiv=False, n_grounds=6),
    "IPR2020-01559": dict(sotera=False, fintiv=True,  n_grounds=6),
    "IPR2020-01629": dict(sotera=False, fintiv=False, n_grounds=2),
    # ── 2021: Fintiv adoption ramping. ─────────────────────────────────
    "IPR2021-00011": dict(sotera=False, fintiv=False, n_grounds=6),
    "IPR2021-00347": dict(sotera=False, fintiv=False, n_grounds=3),
    # 3 distinct narrative-ordinal Fintiv factors ("second/fourth/sixth
    # Fintiv factor") — fires under the ordinal-narrative pattern.
    "IPR2021-00389": dict(sotera=True,  fintiv=True,  n_grounds=5),
    # Six colon-headed factor sections "Factor One:", "Factor Two:",
    # ..., "Factor Six:" with substantive per-factor analysis — fires
    # under the WORDNUM pattern (digit-only colon/keyword patterns
    # don't catch the word-form ordinals).
    "IPR2021-00398": dict(sotera=False, fintiv=True, n_grounds=4),
    # 3 headed factors (4, 5, 6) — fires under the keyword pattern.
    "IPR2021-00455": dict(sotera=False, fintiv=True,  n_grounds=1),
    # Inline factor parentheticals — no headed structure, so does NOT
    # fire by design (3-of-6 headed threshold filters passing mentions).
    "IPR2021-00899": dict(sotera=False, fintiv=False, n_grounds=3),
    # Real Sotera with the canonical "in accordance with Sotera, Petitioners
    # have stipulated... will not pursue in the litigation the specific
    # grounds asserted in the IPR" phrasing.
    "IPR2021-00981": dict(sotera=True,  fintiv=False, n_grounds=3),
    # "will not pursue invalidity in the litigation" — verb is "pursue",
    # target is "invalidity" not "grounds".
    "IPR2021-01461": dict(sotera=True,  fintiv=False, n_grounds=3),
    # ── 2022: peak Fintiv discretionary-denial era. ────────────────────
    # "has stipulated... will not pursue invalidity in the District Court
    # Case on the same grounds" + full headed Fintiv (Factor 1-6 + "Fintiv
    # Factor 5" subheader).
    "IPR2022-00078": dict(sotera=True,  fintiv=True,  n_grounds=0),
    "IPR2022-00304": dict(sotera=False, fintiv=False, n_grounds=2),
    # All 6 narrative-ordinal Fintiv factors ("first/second/.../sixth
    # Fintiv factor") — fires under the ordinal-narrative pattern.
    "IPR2022-00385": dict(sotera=False, fintiv=True,  n_grounds=6),
    "IPR2022-00397": dict(sotera=False, fintiv=False, n_grounds=5),
    "IPR2022-00838": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2022-00938": dict(sotera=False, fintiv=False, n_grounds=0),
    # Civil-procedure stipulation only ("Stipulated Motion to Consolidate")
    # — not a Sotera commitment. Full headed Fintiv (Factor 1-4, 6).
    # n_grounds=0 because "Ground 35 U.S.C. § Claims..." is the table
    # column header, not Ground 35.
    "IPR2022-01011": dict(sotera=False, fintiv=True,  n_grounds=0),
    # "Petitioner also stipulates that... will not pursue the grounds
    # identified in this Petition before the district court" + all 6
    # narrative-ordinal Fintiv factors — both signals fire.
    "IPR2022-01124": dict(sotera=True,  fintiv=True,  n_grounds=1),
    # Plural "Grounds 1 and 2" (shared subsection) + singular Ground 3/
    # 4/5 → 5 distinct, captured by the plural-form alternative branch.
    "IPR2022-01448": dict(sotera=False, fintiv=False, n_grounds=5),
    # All 6 narrative-ordinal Fintiv factors — fires under the
    # ordinal-narrative pattern.
    "IPR2022-01543": dict(sotera=False, fintiv=True,  n_grounds=3),
    # ── 2023: Director Vidal interim DR memo (June 2022); narrowed framework. ──
    "IPR2023-00418": dict(sotera=False, fintiv=False, n_grounds=1),
    "IPR2023-00423": dict(sotera=False, fintiv=False, n_grounds=2),
    # Peak-Fintiv petition: full structure + Sotera stip.
    "IPR2023-00863": dict(sotera=True,  fintiv=True,  n_grounds=3),
    # "stipulates... will not argue in the Litigation invalidity... based
    # on the grounds" — the verb "argue" required adding to the verb
    # alternation. Full Fintiv (Factor 1-6 colon-headed).
    "IPR2023-01117": dict(sotera=True,  fintiv=True,  n_grounds=4),
    # Sotera invoked via exhibit reference only ("Petitioner is presenting
    # a Sotera stipulation. Ex-1021") — the actual stipulation language is
    # in the exhibit, not the petition body. Does NOT fire by design.
    # Fintiv: structural canonical-keyword numbered headings ("3.
    # investment in the parallel proceeding", "4. overlap...", "6. other
    # circumstances...") fire under the new heading-pattern signal.
    "IPR2023-01406": dict(sotera=False, fintiv=True,  n_grounds=0),
    # 3 headed factors (2, 3, 5) inline-headed; fires under colon pattern.
    "IPR2023-01425": dict(sotera=True,  fintiv=True,  n_grounds=1),
    # Petitioner uses non-canonical headings ("Factor 2: The Docket Control
    # Order..." instead of "Factor 2: Trial Date") — keyword pattern misses
    # them but the colon-anchored fallback fires.
    "IPR2023-01441": dict(sotera=True,  fintiv=True,  n_grounds=6),
    # ── 2024: Director Squires; Oct-2024 guidance walks back Fintiv. ───
    # "stipulates not to pursue ... grounds" with PDF page-break footer
    # interrupting between "pursue" and "grounds" — see `parse/schemas/
    # patterns.py::PETITION_SOTERA_PATTERNS` for the period-class fix.
    "IPR2024-00117": dict(sotera=True,  fintiv=False, n_grounds=6),
    "IPR2024-00378": dict(sotera=False, fintiv=False, n_grounds=3),
    # "Patent Owner will not raise any discretionary institution doctrine"
    # — predicting opposing party's behavior, not a petitioner stipulation.
    "IPR2024-00725": dict(sotera=False, fintiv=False, n_grounds=4),
    "IPR2024-00825": dict(sotera=False, fintiv=False, n_grounds=5),
    # Case-law citation about stipulations only, not a real petitioner
    # commitment. Fintiv: structural canonical-keyword numbered headings
    # ("1. Stay", "2. Trial Date", "4. Overlap", "6. Other considerations")
    # fire under the heading-pattern signal — TOC and body both have them.
    "IPR2024-01267": dict(sotera=False, fintiv=True,  n_grounds=2),
    "IPR2024-01302": dict(sotera=False, fintiv=False, n_grounds=6),
    # ── 2025+: post-Squires interim-DR-2025 framework; Fintiv use rare. ─
    # "stipulates... will not pursue in the Parallel Proceedings the
    # specific grounds". 9 Fintiv mentions but no factor-headed structure.
    "IPR2025-00149": dict(sotera=True,  fintiv=False, n_grounds=2),
    # "stipulates that it will not advance ... grounds" — the verb "advance"
    # had to be added to the pattern alternation.
    "IPR2025-00245": dict(sotera=True,  fintiv=False, n_grounds=2),
    # Sotera + structural canonical-keyword numbered headings ("3.
    # Investment in the Parallel Proceeding", "4. Overlapping Issues...",
    # "6. Other Circumstances") under "IX. DISCRETIONARY DENIAL ...
    # No Discretionary Denial Under Fintiv".
    "IPR2025-00379": dict(sotera=True,  fintiv=True,  n_grounds=5),
    "IPR2025-00867": dict(sotera=False, fintiv=False, n_grounds=4),
    "IPR2025-01359": dict(sotera=False, fintiv=False, n_grounds=1),
    "IPR2026-00156": dict(sotera=False, fintiv=False, n_grounds=0),
    # ── Cohort expansion (May 2026): +45 stratified trials. Adds the ──
    #    "ground 357" (electrical-component, lowercase) and "ground\n56"
    #    (pdfplumber line-wrap) false-positive traps that drove the
    #    `PETITION_GROUND_HEADER_PATTERN` precision tightening (case-
    #    sensitive + horizontal-only whitespace), and the IPR2021-00606
    #    narrative "first/second Fintiv factor" form that drove the
    #    new `PETITION_FINTIV_FACTOR_ORDINAL_PATTERN` detection signal.
    # ── Pre-Fintiv-era extension. ──────────────────────────────────────
    "IPR2018-01006": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2018-01359": dict(sotera=False, fintiv=False, n_grounds=6),
    "IPR2018-01494": dict(sotera=False, fintiv=False, n_grounds=6),
    # Petition uses "Challenge #1/2/3" terminology, not "Ground N" —
    # captured by the alternative-enumeration branch of
    # `PETITION_GROUND_HEADER_PATTERN` (added after the May 2026
    # False-sweep audit surfaced this petitioner-style recall gap).
    "IPR2019-00443": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2019-00711": dict(sotera=False, fintiv=False, n_grounds=5),
    "IPR2019-00874": dict(sotera=False, fintiv=False, n_grounds=4),
    "IPR2019-01283": dict(sotera=False, fintiv=False, n_grounds=2),
    # ── 2020. ──────────────────────────────────────────────────────────
    "IPR2020-00306": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2020-00770": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2020-00784": dict(sotera=False, fintiv=False, n_grounds=1),
    "IPR2020-01026": dict(sotera=False, fintiv=False, n_grounds=7),
    # ── 2021. ──────────────────────────────────────────────────────────
    "IPR2021-00080": dict(sotera=False, fintiv=False, n_grounds=5),
    # No formal Ground enumeration; petition argues claims unpatentable
    # over a single combination without numbered grounds.
    "IPR2021-00500": dict(sotera=False, fintiv=False, n_grounds=0),
    # Sotera + 6 narrative-ordinal factors ("first/second/third/fourth
    # Fintiv factor"); colon-keyword patterns miss this form, the
    # ordinal pattern catches it.
    "IPR2021-00606": dict(sotera=True,  fintiv=True,  n_grounds=4),
    "IPR2021-01369": dict(sotera=True,  fintiv=False, n_grounds=4),
    # Full structural Fintiv ("(a) Fintiv Factor 1: ...") + Sotera stip.
    "IPR2021-01439": dict(sotera=True,  fintiv=True,  n_grounds=3),
    # ── 2022. ──────────────────────────────────────────────────────────
    "IPR2022-00100": dict(sotera=True,  fintiv=False, n_grounds=3),
    # Sub-grounds 1A-1G all share prefix "1" → n_grounds=1.
    "IPR2022-00242": dict(sotera=False, fintiv=False, n_grounds=1),
    # Full Fintiv (Factor 1-4 colon-headed) + Sotera stip; sub-grounds
    # 1A/1B/2A/2B → distinct prefixes {1,2}.
    "IPR2022-00500": dict(sotera=True,  fintiv=True,  n_grounds=2),
    # Canonical case-study trial (docs/features/admissible_documents
    # _analysis.md §2). Sotera fires on "will cease asserting ...
    # invalidity". The Fintiv discussion uses bare canonical-keyword
    # numbered subsections ("1. No evidence regarding a stay",
    # "2. Parallel proceeding trial date", "3. Investment in parallel
    # proceeding", "4. Overlapping issues", "5. Petitioner status",
    # "6. Other circumstances") under "IV. DISCRETIONARY CONSIDERATIONS"
    # — captured by the canonical-keyword heading-pattern signal
    # added after the May 2026 False-sweep audit. Reference values
    # in admissible_documents_analysis.md §2.5/2.6.
    "IPR2022-01002": dict(sotera=True,  fintiv=True,  n_grounds=1),
    # Sotera presented via reference to exhibit (TIKTOK-1021), but
    # the petition body itself contains the binding "stipulation
    # not to pursue ... the same grounds" language — fires.
    "IPR2022-01547": dict(sotera=True,  fintiv=False, n_grounds=2),
    # ── 2023. ──────────────────────────────────────────────────────────
    # Per-factor narrative with trailing "See Fintiv factor N" citation
    # anchors (factors 2, 3, 4, 6) plus "Fintiv factors 1 and 5 are
    # either neutral" — 6 distinct factors, captured by the
    # ANCHORED_DIGIT pattern.
    "IPR2023-00016": dict(sotera=False, fintiv=True, n_grounds=4),
    # Sotera body language with full §"PETITIONER'S SOTERA STIPULATION"
    # heading; sub-grounds 1A/1B/2A/3A/4A/2B-4B → {1,2,3,4}.
    "IPR2023-00228": dict(sotera=True,  fintiv=False, n_grounds=4),
    "IPR2023-00254": dict(sotera=False, fintiv=False, n_grounds=2),
    # "Petitioner reserves the right to submit a stipulation at a later
    # time" — a reservation, not a stipulation. Does NOT fire.
    # Six line-anchored "Factor N is/favors..." sentences with
    # substantive per-factor reasoning, no colon/period delimiter —
    # fires under the LINE pattern.
    "IPR2023-01048": dict(sotera=False, fintiv=True, n_grounds=2),
    "IPR2023-01090": dict(sotera=True,  fintiv=False, n_grounds=10),
    "IPR2023-01274": dict(sotera=True,  fintiv=False, n_grounds=4),
    # ── 2024. ──────────────────────────────────────────────────────────
    # "Factor 4 (overlapping issues) favors institution. ... Factor 5
    # (same parties) ... Factor 6 (other considerations)" — keyword
    # pattern fires on "(overlapping" within the post-"Factor 4" window.
    "IPR2024-00349": dict(sotera=True,  fintiv=True,  n_grounds=2),
    "IPR2024-00797": dict(sotera=False, fintiv=False, n_grounds=2),
    # ── 2025. ──────────────────────────────────────────────────────────
    "IPR2025-00010": dict(sotera=False, fintiv=False, n_grounds=2),
    # Single narrative mention "the third Fintiv factor" alone, not 3
    # distinct ordinals — ordinal-threshold filter prevents firing.
    "IPR2025-00200": dict(sotera=False, fintiv=False, n_grounds=2),
    # Sotera + structural canonical-keyword numbered headings ("3.
    # Investment in the parallel proceeding", "4. Overlapping issues
    # with the parallel proceeding", "6. Other circumstances").
    "IPR2025-00241": dict(sotera=True,  fintiv=True,  n_grounds=2),
    # Sub-grounds 1A/1B → {1}.
    "IPR2025-00896": dict(sotera=False, fintiv=False, n_grounds=1),
    "IPR2025-01188": dict(sotera=False, fintiv=False, n_grounds=4),
    # Stipulation is referenced only via exhibit list ("EX-1020
    # Petitioner's August 28, 2025 Stipulation"); no body commitment
    # language — does NOT fire (mirrors IPR2023-01406 / IPR2025-01446
    # exhibit-only convention).
    "IPR2025-01446": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2025-01589": dict(sotera=False, fintiv=False, n_grounds=5),
    # ── 2026. ──────────────────────────────────────────────────────────
    # FP traps: "ground 357" (electrical-component reference,
    # lowercase) caught by the case-sensitive guard; "ground\n56" PDF
    # line-wrap caught by the horizontal-whitespace guard.
    "IPR2026-00194": dict(sotera=True,  fintiv=False, n_grounds=6),
    "IPR2026-00229": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2026-00291": dict(sotera=False, fintiv=False, n_grounds=5),
    # IPR2026-00338 is the canonical "ground\n56" line-wrap trap —
    # before the `[ \t]+` fix, regex picked up a hyphenated "ground"
    # at line-end paired with the next line's "56" page number.
    "IPR2026-00338": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2026-00339": dict(sotera=False, fintiv=False, n_grounds=10),
    "IPR2026-00340": dict(sotera=False, fintiv=False, n_grounds=3),
    # Sub-grounds 1A-F + 2A-B → {1, 2}.
    "IPR2026-00342": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2026-00343": dict(sotera=False, fintiv=False, n_grounds=3),
    "PGR2026-00026": dict(sotera=False, fintiv=False, n_grounds=3),
    # ── Cohort expansion #2 (May 2026): +29 stratified trials. Adds ──
    #    another narrative-ordinal Fintiv petition (IPR2025-00062 — full
    #    "first/second/.../sixth Fintiv factor" walkthrough), exhibit-
    #    name false-positive trap (IPR2024-00710 has "Ulead DVD
    #    MovieFactor 2 User Guide" in the exhibit list — the keyword
    #    pattern correctly skips because no canonical-keyword follows),
    #    inline-parenthetical-only Fintiv discussions that must NOT fire
    #    (IPR2022-01412), and several pre-2017 / 2018 / 2019 patents
    #    with no Fintiv (Apple v. Fintiv was 2020-03-20).
    # ── Pre-Fintiv era. ────────────────────────────────────────────────
    "IPR2017-00813": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2017-00881": dict(sotera=False, fintiv=False, n_grounds=0),
    "IPR2017-00976": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2018-01149": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2018-01253": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2019-00067": dict(sotera=False, fintiv=False, n_grounds=0),
    "IPR2019-00850": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2019-01391": dict(sotera=False, fintiv=False, n_grounds=3),
    # ── 2020. ──────────────────────────────────────────────────────────
    "IPR2020-00004": dict(sotera=False, fintiv=False, n_grounds=4),
    "IPR2020-00403": dict(sotera=False, fintiv=False, n_grounds=4),
    "IPR2020-00605": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2020-00898": dict(sotera=False, fintiv=False, n_grounds=9),
    # ── 2021. ──────────────────────────────────────────────────────────
    # No Fintiv factor briefing — Section "V.A. Discretionary Denial Under
    # Fintiv factors is not appropriate" but no per-factor structure or
    # ordinal narrative.
    "IPR2021-01032": dict(sotera=False, fintiv=False, n_grounds=5),
    # No Ground enumeration; petition issues a single combined invalidity
    # challenge without "Ground N" markers.
    "IPR2021-00499": dict(sotera=False, fintiv=False, n_grounds=0),
    # Sotera ("Samsung stipulates that it will not pursue invalidity ...");
    # Fintiv discussion uses inline "this factor" prose without per-factor
    # numbers or headed structure.
    "IPR2021-01220": dict(sotera=True,  fintiv=False, n_grounds=4),
    # ── 2022. ──────────────────────────────────────────────────────────
    "IPR2022-01108": dict(sotera=False, fintiv=False, n_grounds=5),
    # Heading-style "Fintiv Factors 3, 4, and 6 Favor Institution: ..."
    # and "Fintiv Factors 1 and 2 are Neutral and Even When Combined
    # with Factor 5..." cover all 6 factors via the ANCHORED_DIGIT
    # pattern. Inline "(Factor N)" parentheticals also appear but are
    # not the load-bearing signal.
    "IPR2022-01412": dict(sotera=False, fintiv=True, n_grounds=3),
    # ── 2023. ──────────────────────────────────────────────────────────
    # Narrative "No Fintiv factor justifies" + 3 case-citation Fintiv
    # mentions — no structural briefing, threshold filter holds.
    "IPR2023-00022": dict(sotera=False, fintiv=False, n_grounds=0),
    "IPR2023-00617": dict(sotera=False, fintiv=False, n_grounds=4),
    # Single inline "Fintiv factor 6" mention only — well below threshold.
    "IPR2023-01284": dict(sotera=False, fintiv=False, n_grounds=2),
    # ── 2024. ──────────────────────────────────────────────────────────
    # 6 inline "this factor" mentions but never with factor numbers or
    # the "Fintiv factor" anchor — neither keyword nor ordinal fires.
    "IPR2024-00212": dict(sotera=False, fintiv=False, n_grounds=5),
    # Only "Ground 35" appears (in "Ground 35 U.S.C. § 102/103" table
    # header) — negative lookahead correctly rejects it.
    "IPR2024-00275": dict(sotera=False, fintiv=False, n_grounds=0),
    # Exhibit-name FP trap: "Ulead DVD MovieFactor 2 User Guide" is an
    # exhibit name, not a Fintiv factor heading — keyword pattern
    # correctly skips (no canonical keyword follows the digit).
    "IPR2024-00710": dict(sotera=False, fintiv=False, n_grounds=5),
    # ── 2025. ──────────────────────────────────────────────────────────
    # Full 6-factor narrative-ordinal Fintiv ("first/second/.../sixth
    # Fintiv factor") — fires under the ordinal pattern, mirroring
    # IPR2021-00606 / IPR2022-00385 / IPR2022-01124 / IPR2022-01543.
    "IPR2025-00062": dict(sotera=False, fintiv=True,  n_grounds=6),
    "IPR2025-01569": dict(sotera=False, fintiv=False, n_grounds=2),
    # ── 2026. ──────────────────────────────────────────────────────────
    "IPR2026-00113": dict(sotera=False, fintiv=False, n_grounds=2),
    "IPR2026-00258": dict(sotera=False, fintiv=False, n_grounds=3),
    "IPR2026-00275": dict(sotera=False, fintiv=False, n_grounds=7),
    # "Grounds 1-3" (range) + "Grounds 4 and 5" (plural-and) → 5
    # distinct; plural-and branch fires on the second.
    "PGR2026-00028": dict(sotera=False, fintiv=False, n_grounds=5),
}


def _cache_path(trial: str) -> Path:
    """Resolve the cached petition-text path for a trial."""
    return raw_dir() / Stage.PETITION_TEXTS.value / f"{trial}.txt"


def _load_cached(trial: str) -> str | None:
    """Read the cached petition text, or `None` if not present."""
    p = _cache_path(trial)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def cohort_size() -> int:
    """Number of cohort trials with cached blobs (skip-aware bookkeeping)."""
    return sum(1 for trial in PETITION_GROUND_TRUTH if _cache_path(trial).exists())


@pytest.mark.parametrize("trial,truth", list(PETITION_GROUND_TRUTH.items()))
def test_petition_features_match_audited_truth(trial: str, truth: dict) -> None:
    """Each cohort trial's extracted features must match the manual audit."""
    text = _load_cached(trial)
    if text is None:
        pytest.skip(f"petition text blob not cached: {trial}")

    feat = extract_features(text)

    assert feat["has_sotera_stipulation"] == int(truth["sotera"]), (
        f"{trial}: sotera got {feat['has_sotera_stipulation']}, want {truth['sotera']}"
    )
    assert feat["mentions_fintiv_factors"] == int(truth["fintiv"]), (
        f"{trial}: fintiv got {feat['mentions_fintiv_factors']}, want {truth['fintiv']}"
    )
    assert feat["n_grounds"] == truth["n_grounds"], (
        f"{trial}: n_grounds got {feat['n_grounds']}, want {truth['n_grounds']}"
    )


# ──────────────────────────────────────────────────────────────────────
# Strict manual-audit batch: 30 unaudited petitions, each manually
# read to derive ground-truth values for all 5 Tier A features
# (n_grounds, sotera, fintiv, has_102, has_103). Records the LEGAL
# CONCEPT (e.g. plural "Grounds 1 and 2" → 2 distinct grounds;
# written-out "Factor one/two/.../six" walking 6 factors → fintiv=True),
# so each entry's comments capture the salient phrasing the regex
# matched on — not what would have been a recall gap before the
# corresponding pattern branch existed.
PETITION_NEW_AUDIT_TRUTH: dict[str, dict] = {
    # ── Pre-Fintiv era. ────────────────────────────────────────────────
    "IPR2019-00979": dict(n_grounds=3, sotera=False, fintiv=False, has_102=True, has_103=True),
    # Plural "Grounds 1 and 2" + singular Ground 3-6 → 6 distinct;
    # plural-form branch contributes digits 1 and 2.
    "IPR2020-01438": dict(n_grounds=6, sotera=False, fintiv=False, has_102=True, has_103=True),
    "IPR2020-01534": dict(n_grounds=4, sotera=False, fintiv=False, has_102=True, has_103=True),
    # Full Factor 1-6 walkthrough — keyword pattern fires.
    "IPR2021-00370": dict(n_grounds=1, sotera=False, fintiv=True, has_102=True, has_103=True),
    # Single case-citation Fintiv mention only — no per-factor analysis.
    "IPR2022-00687": dict(n_grounds=3, sotera=False, fintiv=False, has_102=True, has_103=True),
    # ── 2022. ──────────────────────────────────────────────────────────
    # "Sotera-type stipulation in EX1020" — exhibit-only reference; no
    # body commitment language. Per cohort convention: sotera=False.
    "IPR2022-01435": dict(n_grounds=5, sotera=False, fintiv=False, has_102=True, has_103=True),
    # ── 2023. ──────────────────────────────────────────────────────────
    "IPR2023-00667": dict(n_grounds=2, sotera=False, fintiv=False, has_102=True, has_103=True),
    # ── 2024. ──────────────────────────────────────────────────────────
    # 6-factor narrative-ordinal Fintiv ("first/second/.../sixth Fintiv
    # factor") fires under ordinal pattern. has_103=False because the
    # petition argues obviousness without ever citing the §103 statute
    # by name (only "rendered obvious" prose).
    "IPR2024-00056": dict(n_grounds=4, sotera=False, fintiv=True, has_102=True, has_103=False),
    # 15 enumerated Grounds — stress-tests upper-range counting.
    # Sotera body language fires on "Petitioner hereby stipulates that
    # ... will not raise ... any defense based on the same grounds".
    "IPR2024-00109": dict(n_grounds=15, sotera=True, fintiv=False, has_102=True, has_103=True),
    # Letter-only grounds (A, B) → n_grounds=0 per cohort convention.
    # Written-out ordinals "Factor one/two/.../six" walking all 6
    # factors substantively — fires under WORDNUM pattern.
    "IPR2024-00323": dict(n_grounds=0, sotera=True, fintiv=True, has_102=True, has_103=True),
    "IPR2024-00605": dict(n_grounds=3, sotera=False, fintiv=False, has_102=True, has_103=True),
    # Sotera body fires on "Dell has offered a stipulation ... not to
    # pursue ... the same grounds". Fintiv: "Fintiv factor 1/2/3"
    # canonical keyword form. has_103 fires via the line-anchored
    # grounds-table-row branch on bare "1 103 ..." / "2 103 ..." (the
    # column header is "Ground Claims Prior Art" with no separate §).
    "IPR2024-00684": dict(n_grounds=2, sotera=True, fintiv=True, has_102=True, has_103=True),
    # 6-factor analysis under bare numbered subheadings ("1. District
    # Court Stay" / "2. Proximity of Trial Date" / ...) fires under
    # heading pattern. has_103 fires on "35 U.S.C. §§ 102/103" via the
    # double-§-with-slash-bridging branch.
    "IPR2024-00987": dict(n_grounds=5, sotera=False, fintiv=True, has_102=True, has_103=True),
    # Sotera fires on "Medela LLC stipulates that it will not rely on
    # any grounds in the district court that were raised or reasonably
    # could have been raised in this Petition" — verb "rely" is in the
    # alternation.
    "IPR2024-01076": dict(n_grounds=5, sotera=True, fintiv=True, has_102=True, has_103=True),
    # 6-factor analysis under bare numbered subheadings — heading pattern.
    "IPR2024-01124": dict(n_grounds=2, sotera=False, fintiv=True, has_102=True, has_103=True),
    "IPR2024-01134": dict(n_grounds=3, sotera=False, fintiv=True, has_102=True, has_103=True),
    # Sub-grounds 1A-1D share prefix 1.
    "IPR2024-01296": dict(n_grounds=1, sotera=False, fintiv=True, has_102=True, has_103=True),
    # Sotera fires on "Petitioner stipulates not to seek resolution in
    # the district court of the same grounds" (verb "seek"). Fintiv:
    # line-anchored "Factor 1 is neutral; Factor 2 is neutral; ..." —
    # only Factor 1 has a canonical keyword in proximity, but the LINE
    # pattern catches all 6.
    "IPR2024-01463": dict(n_grounds=2, sotera=True, fintiv=True, has_102=True, has_103=True),
    # ── 2025. ──────────────────────────────────────────────────────────
    "IPR2025-00341": dict(n_grounds=2, sotera=False, fintiv=True, has_102=True, has_103=True),
    # Sotera body language fires. Fintiv: section relies on Sotera
    # stipulation + Interim Procedure citation — no per-factor
    # walkthrough → fintiv=False.
    "IPR2025-00529": dict(n_grounds=5, sotera=True, fintiv=False, has_102=True, has_103=True),
    # Narrative analysis of factors 1, 2, 3, 4, 6 with literal "Fintiv
    # factor N" and "Fintiv factors N and M" anchors throughout —
    # ANCHORED_DIGIT pattern collects all 5 distinct factors.
    "IPR2025-00555": dict(n_grounds=1, sotera=False, fintiv=True, has_102=True, has_103=True),
    "IPR2025-00772": dict(n_grounds=3, sotera=False, fintiv=False, has_102=True, has_103=True),
    "IPR2025-01010": dict(n_grounds=5, sotera=True, fintiv=True, has_102=True, has_103=True),
    "IPR2025-01034": dict(n_grounds=11, sotera=False, fintiv=False, has_102=True, has_103=True),
    "IPR2025-01142": dict(n_grounds=1, sotera=False, fintiv=False, has_102=True, has_103=False),
    "IPR2025-01325": dict(n_grounds=2, sotera=False, fintiv=False, has_102=True, has_103=True),
    # Statute citations live only in table form: prior-art rows like
    # "(EX1005) 102(b)" fire on the subsection-notation branch,
    # grounds-table rows "1 103 1, 11, 14, 15 Park" fire on the
    # line-anchored row branch.
    "IPR2025-01498": dict(n_grounds=10, sotera=False, fintiv=False, has_102=True, has_103=True),
    "IPR2025-01560": dict(n_grounds=4, sotera=False, fintiv=False, has_102=True, has_103=False),
    # ── 2026. ──────────────────────────────────────────────────────────
    "IPR2026-00050": dict(n_grounds=7, sotera=False, fintiv=False, has_102=True, has_103=True),
    "IPR2026-00227": dict(n_grounds=3, sotera=False, fintiv=False, has_102=True, has_103=True),
}


@pytest.mark.parametrize("trial,truth", list(PETITION_NEW_AUDIT_TRUTH.items()))
def test_new_audit_features_match_manual_truth(trial: str, truth: dict) -> None:
    """Strict manual-audit cohort: each trial's regex output must match
    the manually-derived legal-concept ground truth.

    Failures here surface real recall/precision gaps in the regex set
    (not test drift). Each entry's comments document the phrasing
    pattern that drives the value.
    """
    text = _load_cached(trial)
    if text is None:
        pytest.skip(f"petition text blob not cached: {trial}")

    feat = extract_features(text)

    assert feat["n_grounds"] == truth["n_grounds"], (
        f"{trial}: n_grounds got {feat['n_grounds']}, want {truth['n_grounds']}"
    )
    assert feat["has_sotera_stipulation"] == int(truth["sotera"]), (
        f"{trial}: sotera got {feat['has_sotera_stipulation']}, want {truth['sotera']}"
    )
    assert feat["mentions_fintiv_factors"] == int(truth["fintiv"]), (
        f"{trial}: fintiv got {feat['mentions_fintiv_factors']}, want {truth['fintiv']}"
    )
    assert (feat["n_grounds_102"] > 0) == truth["has_102"], (
        f"{trial}: has_102 got {feat['n_grounds_102'] > 0} (count={feat['n_grounds_102']}), want {truth['has_102']}"
    )
    assert (feat["n_grounds_103"] > 0) == truth["has_103"], (
        f"{trial}: has_103 got {feat['n_grounds_103'] > 0} (count={feat['n_grounds_103']}), want {truth['has_103']}"
    )


def test_cohort_minimum_coverage(cohort_size: int) -> None:
    """At least 5 cohort blobs must be cached for the suite to be meaningful.

    The cohort spans every phrasing variant we know about, so running with
    fewer than 5 cached blobs almost certainly means the petition-text
    cache is missing — re-fetch via `drivers/run_ingest_petition_pdfs.py`
    to repopulate. With zero cached blobs every per-trial test skips
    silently and the regex regression suite goes effectively dark.
    """
    if cohort_size == 0:
        pytest.skip("no petition text blobs cached — run drivers/run_ingest_petition_pdfs.py")
    assert cohort_size >= 5, (
        f"only {cohort_size}/{len(PETITION_GROUND_TRUTH)} cohort blobs cached; "
        "the integration suite is effectively dark — repopulate the cache"
    )
