"""Tests for the Tier A petition-text regex aggregator + the upstream usability filter."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml_uspto.features.schemas.constants import MIN_PETITION_TEXT_CHARS
from ml_uspto.features.petition_text import (
    aggregate_petition_text_row as extract_petition_text_features,
    select_usable_rows,
)


def test_n_grounds_dedupes_repeated_citations():
    """Petitioners cite ground numbers many times; n_grounds counts distinct indices."""
    text = (
        "Ground 1: Claims 1-9 are unpatentable under § 103.\n"
        "Ground 2: Claims 10-12 are unpatentable under § 102.\n"
        "As shown in Ground 1, Patent Owner argued ... see also Ground 1.\n"
        "Ground 1 establishes the Paulraj reference.\n"
    )
    feat = extract_petition_text_features(text)
    assert feat["n_grounds"] == 2


def test_statute_counts():
    """Per-statute counts come from the literal section signs."""
    text = "asserts § 102(e) ... § 103(a) ... another § 103 reference ... § 112 ..."
    feat = extract_petition_text_features(text)
    assert feat["n_grounds_102"] == 1
    assert feat["n_grounds_103"] == 2


def test_sotera_stipulation_will_cease_asserting():
    """The IPR2022-01002 phrasing fires the Sotera flag."""
    text = (
        "Petitioner stipulates that, if instituted, it will cease asserting in the "
        "district court any invalidity contention based on the grounds presented."
    )
    feat = extract_petition_text_features(text)
    assert feat["has_sotera_stipulation"] == 1


def test_sotera_stipulation_will_not_pursue():
    """The shorter 'will not pursue ... grounds' alternative also fires."""
    text = "Petitioner agrees it will not pursue, in the parallel litigation, the same grounds presented herein."
    feat = extract_petition_text_features(text)
    assert feat["has_sotera_stipulation"] == 1


def test_sotera_stipulation_absent():
    """A petition with no stipulation language returns False."""
    text = "This petition challenges claims 1-9 under § 103."
    feat = extract_petition_text_features(text)
    assert feat["has_sotera_stipulation"] == 0


def test_fintiv_factors_six_headers_fires():
    """All six Fintiv-factor subheadings present → flag True."""
    text = (
        "Section IV: Discretionary Considerations\n"
        "Factor 1 — Stay: No motion to stay has been filed.\n"
        "Factor 2 — Trial Date: Jury selection is set for Oct 23.\n"
        "Factor 3 — Investment: Discovery is in early stages.\n"
        "Factor 4 — Overlap: The grounds here do not overlap.\n"
        "Factor 5 — Petitioner: Petitioner is the defendant in the parallel case.\n"
        "Factor 6 — Other circumstances: The merits are strong.\n"
    )
    feat = extract_petition_text_features(text)
    assert feat["mentions_fintiv_factors"] == 1


def test_fintiv_factors_below_threshold():
    """Only two factors mentioned → does not trip the ≥3 threshold."""
    text = "Factor 1 - Stay: none. Factor 6 - other: merits strong."
    feat = extract_petition_text_features(text)
    assert feat["mentions_fintiv_factors"] == 0


def test_fintiv_passing_mention_does_not_fire():
    """Mentioning 'Fintiv' in passing without per-factor structure → False."""
    text = "Petitioner notes the Fintiv framework is applicable but does not rely on it."
    feat = extract_petition_text_features(text)
    assert feat["mentions_fintiv_factors"] == 0


def test_ipr2022_01002_regression():
    """Canonical regression — extracted features match the case-study reference values.

    Reference: docs/engineering/features/admissible_documents_analysis.md §2.5/2.6.
    Synthetic-but-faithful slice of the IPR2022-01002 petition.
    """
    text = (
        "Petition for Inter Partes Review of U.S. Patent No. 9,191,083\n"
        "I. REQUIREMENTS FOR INTER PARTES REVIEW\n"
        "  Ground 1: Claims 1-9, 12-20 are unpatentable under § 103 — "
        "Paulraj in view of Heath.\n"
        "IV. DISCRETIONARY CONSIDERATIONS UNDER § 314(a)\n"
        "  Factor 1 - Stay: No stay motion has been filed.\n"
        "  Factor 2 - Trial Date: Jury selection 2023-10-23.\n"
        "  Factor 3 - Investment: minimal; discovery closes 2023-03-29.\n"
        "  Factor 4 - Overlap: Petitioner stipulates that, if instituted, it "
        "will cease asserting in the district court any invalidity contention "
        "based on the grounds presented.\n"
        "  Factor 5 - Petitioner: Defendant.\n"
        "  Factor 6 - Other: merits are strong.\n"
        "CERTIFICATE OF WORD COUNT\n"
        "This Petition contains 13,949 words.\n"
    )
    feat = extract_petition_text_features(text)
    assert feat["n_grounds"] == 1
    assert feat["n_grounds_103"] == 1
    assert feat["n_grounds_102"] == 0
    assert feat["has_sotera_stipulation"] == 1
    assert feat["mentions_fintiv_factors"] == 1


# ---------------------------------------------------------------------------
# select_usable_rows — upstream filter that gates the aggregator
# ---------------------------------------------------------------------------


def _frame_with_texts(texts: list[object]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trial_number": [f"IPR2024-{i:05d}" for i in range(len(texts))],
            "petition_text": texts,
        }
    )


def testselect_usable_rows_drops_nan_and_blank_sentinel():
    """NaN cache misses and the ingest driver's literal 'BLANK' sentinel both drop."""
    frame = _frame_with_texts([np.nan, "BLANK", "x" * MIN_PETITION_TEXT_CHARS])
    out = select_usable_rows(frame)
    assert list(out["trial_number"]) == ["IPR2024-00002"]


def testselect_usable_rows_drops_whitespace_only_short_text():
    """All-whitespace pdfplumber output is below the threshold and drops."""
    frame = _frame_with_texts(["\n" * 100, "x" * MIN_PETITION_TEXT_CHARS])
    out = select_usable_rows(frame)
    assert len(out) == 1


def testselect_usable_rows_keeps_text_at_threshold():
    """Text whose length equals the threshold is the inclusion boundary."""
    frame = _frame_with_texts(
        [
            "x" * (MIN_PETITION_TEXT_CHARS - 1),
            "x" * MIN_PETITION_TEXT_CHARS,
            "x" * (MIN_PETITION_TEXT_CHARS + 1),
        ]
    )
    out = select_usable_rows(frame)
    assert list(out["trial_number"]) == ["IPR2024-00001", "IPR2024-00002"]


def testselect_usable_rows_no_op_when_column_missing():
    """Missing `petition_text` column → no-op, mirrors the conditional Tier A path."""
    frame = pd.DataFrame({"trial_number": ["IPR2024-00001"]})
    out = select_usable_rows(frame)
    assert len(out) == 1
    assert list(out.columns) == ["trial_number"]


def testselect_usable_rows_returns_copy():
    """Mutating the returned frame must not write back through to the input."""
    frame = _frame_with_texts(["x" * MIN_PETITION_TEXT_CHARS])
    out = select_usable_rows(frame)
    out.iloc[0, out.columns.get_loc("petition_text")] = "mutated"
    assert frame.iloc[0]["petition_text"] != "mutated"
