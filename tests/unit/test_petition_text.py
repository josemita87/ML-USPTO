"""Tests for the Tier A petition-text regex aggregator in `features.transforms`."""

from __future__ import annotations

from ml_uspto.features.transforms import _aggregate_petition_text_row as extract_petition_text_features


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


def test_empty_text_returns_default_row():
    """Empty/garbled text returns defaults — never raises."""
    feat = extract_petition_text_features("")
    assert feat["n_grounds"] == 0
    assert feat["n_grounds_102"] == 0
    assert feat["n_grounds_103"] == 0
    assert feat["has_sotera_stipulation"] == 0
    assert feat["mentions_fintiv_factors"] == 0


def test_non_string_input_returns_default_row():
    """NaN / None / non-string input returns defaults — joiner can attach NaN."""
    feat = extract_petition_text_features(None)
    assert feat["n_grounds"] == 0
    assert feat["has_sotera_stipulation"] == 0


def test_ipr2022_01002_regression():
    """Canonical regression — extracted features match the case-study reference values.

    Reference: docs/features/admissible_documents_analysis.md §2.5/2.6.
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
