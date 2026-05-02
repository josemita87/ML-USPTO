"""Joined frame → leakage-free intermediate feature matrix.

Patent aggregation reads the parallel-array columns the joiner attaches
from `Frame.PATENTS`; petition-text aggregation reads the
`petition_text` column the joiner attaches from `Frame.PETITION_TEXTS`.
Both are row-local — no cross-row corpus statistics — and apply T₀
leakage discipline where time-bound (patent events).
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from ml_uspto.features.schemas.constants import (
    BANNED_EVENT_CATEGORIES,
    EVENT_CODE_CATEGORIES,
    FREQUENCY_CATEGORICAL_COLUMNS,
    OHE_CATEGORICAL_COLUMNS,
    PATENT_COUNT_FEATURES,
    PATENT_NULLABLE_NUMERIC,
    PETITION_TEXT_FEATURE_KEYS,
    PRESIDENTIAL_REGIMES,
    TRIAL_EVENT_PREFIXES,
)
from ml_uspto.features.schemas.enums import EventCategory
from ml_uspto.features.schemas.patterns import NORMALIZE_NON_ALNUM
from ml_uspto.parse.schemas.patterns import (
    _FINTIV_FACTOR_WORD_TO_INDEX,
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
from ml_uspto.parse.utils import to_date

logger = logging.getLogger(__name__)


_NON_TRIAL_CATEGORIES: tuple[EventCategory, ...] = tuple(
    cat for cat in EventCategory if cat is not EventCategory.TRIAL
)


def _as_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "tolist"):  # numpy / pyarrow array-like
        return list(value)
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    return []


def _event_category(code: str) -> EventCategory:
    if any(code.startswith(prefix) for prefix in TRIAL_EVENT_PREFIXES):
        return EventCategory.TRIAL
    return EVENT_CODE_CATEGORIES.get(code, EventCategory.OTHER)


def _presidential_regime(d: date | None) -> str | None:
    if d is None:
        return None
    label: str | None = None
    for start, name in PRESIDENTIAL_REGIMES:
        if d >= start:
            label = name
        else:
            break
    return label


def _normalize_assignee(name: str) -> str:
    normalized = NORMALIZE_NON_ALNUM.sub(" ", name.lower()).strip()
    return " ".join(normalized.split())


def _aggregate_patent_row(row: pd.Series) -> dict[str, object]:
    """Apply T₀ leakage discipline + count/span aggregation to one joined-frame row.

    T₀ enforcement (per `docs/scope/prediction_scope.md` §4): events
    whose `event_date >= T₀` are dropped before counting, as are events
    whose codes start with a `TRIAL_EVENT_PREFIXES` value or fall in
    `BANNED_EVENT_CATEGORIES`. Assignments require
    `assignment_received_date < T₀` OR `assignment_recorded_date < T₀`
    to count.

    Args:
        row: A single row of the joined frame, carrying
            `petition_filing_date` (T₀) and the patent parallel-array
            columns. NaN/missing arrays are treated as empty.

    Returns:
        Mapping of aggregated feature names to values for this row.
    """
    counts: dict[EventCategory, int] = {cat: 0 for cat in _NON_TRIAL_CATEGORIES}

    t0 = to_date(row.get("petition_filing_date"))
    if t0 is None:
        return {
            "n_events_pre_t0": 0,
            "prosecution_span_days": None,
            **{f"n_{cat.value.lower()}_pre_t0": 0 for cat in _NON_TRIAL_CATEGORIES},
            "n_office_actions": 0,
            "n_assignments_pre_t0": 0,
            "n_distinct_assignees_pre_t0": 0,
            "days_since_last_assignment": None,
            "days_grant_to_petition": None,
            "n_parent_applications": 0,
            "cpc_section": None,
        }

    kept_event_dates: list[date] = []
    for code, raw_d in zip(
        _as_list(row.get("event_codes")),
        _as_list(row.get("event_dates")),
        strict=False,
    ):
        d = to_date(raw_d)
        if d is None or d >= t0:
            continue
        code_str = str(code or "").strip()
        if not code_str:
            continue
        cat = _event_category(code_str)
        if cat in BANNED_EVENT_CATEGORIES:
            continue
        counts[cat] += 1
        kept_event_dates.append(d)

    prosecution_span_days = (
        (max(kept_event_dates) - min(kept_event_dates)).days
        if len(kept_event_dates) >= 2 else None
    )

    pre_t0_assignment_dates: list[date] = []
    distinct_assignees: set[str] = set()
    for r, c, names in zip(
        _as_list(row.get("assignment_received_dates")),
        _as_list(row.get("assignment_recorded_dates")),
        _as_list(row.get("assignees_per_assignment")),
        strict=False,
    ):
        candidates = [d for d in (to_date(r), to_date(c)) if d is not None and d < t0]
        if not candidates:
            continue
        pre_t0_assignment_dates.append(min(candidates))
        for name in _as_list(names):
            if isinstance(name, str) and name:
                normalized = _normalize_assignee(name)
                if normalized:
                    distinct_assignees.add(normalized)

    days_since_last_assignment = (
        (t0 - max(pre_t0_assignment_dates)).days if pre_t0_assignment_dates else None
    )
    grant = to_date(row.get("grant_date"))
    days_grant_to_petition = (t0 - grant).days if grant is not None else None

    cpc_section = next(
        (
            str(code)[0].upper()
            for code in _as_list(row.get("cpc_codes"))
            if isinstance(code, str) and code and code[0].isalpha()
        ),
        None,
    )

    return {
        "n_events_pre_t0": sum(counts.values()),
        "prosecution_span_days": prosecution_span_days,
        **{f"n_{cat.value.lower()}_pre_t0": n for cat, n in counts.items()},
        "n_office_actions": counts[EventCategory.EX],
        "n_assignments_pre_t0": len(pre_t0_assignment_dates),
        "n_distinct_assignees_pre_t0": len(distinct_assignees),
        "days_since_last_assignment": days_since_last_assignment,
        "days_grant_to_petition": days_grant_to_petition,
        "n_parent_applications": len(_as_list(row.get("parent_app_numbers"))),
        "cpc_section": cpc_section,
    }


def _aggregate_patents(joined: pd.DataFrame) -> pd.DataFrame:
    """Per-row T₀ patent aggregation; returns a frame indexed like `joined`."""
    rows = [_aggregate_patent_row(row) for _, row in joined.iterrows()]
    return pd.DataFrame(rows, index=joined.index)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Joined frame → leakage-free intermediate matrix.

    Every output column is a function of one row's own raw fields.
    Cross-row transforms (one-hot column-set, frequency counts, median
    imputation, scaling) are deferred to
    `ml_uspto.models.preprocessing.build_preprocessor`, which fits on
    training rows only inside a CV-aware Pipeline. See
    `docs/features/features_csv_dictionary.md` for the methodology
    note and `docs/features/patent_file_wrapper_features.md`
    §"Missingness semantics" for the four-regime missingness model.

    Args:
        df: Joined frame from `parse.joiner.join_all`.

    Returns:
        Feature frame with calendar/row-local numerics
        (`filing_year`, `filing_month`, `filing_dayofweek`,
        `art_unit_group`); T₀-filtered patent aggregates
        (`n_*_pre_t0`, `n_office_actions`,
        `n_distinct_assignees_pre_t0`, `n_parent_applications`);
        nullable scalars (`prosecution_span_days`,
        `days_grant_to_petition`, `days_since_last_assignment`) paired
        with their `_missing` regime indicators set *before* any
        imputation; raw categoricals for the downstream encoder
        (`technology_center`, `cpc_section`, `presidential_regime`,
        `petitioner_real_party`, `owner_real_party`). The label column
        (`cancelled`) is not
        part of the returned frame — callers attach it back from `df`.
    """
    patent_features = _aggregate_patents(df)
    augmented = pd.concat(
        [df.reset_index(drop=True), patent_features.reset_index(drop=True)],
        axis=1,
    )

    features = pd.DataFrame(index=augmented.index)

    if "petition_filing_date" in augmented.columns:
        pf = pd.to_datetime(augmented["petition_filing_date"], errors="coerce")
        features["filing_year"] = pf.dt.year.astype("Int64")
        features["filing_month"] = pf.dt.month.astype("Int64")
        features["filing_dayofweek"] = pf.dt.dayofweek.astype("Int64")
        features["presidential_regime"] = (
            augmented["petition_filing_date"].map(to_date).map(_presidential_regime)
        )

    if "group_art_unit" in augmented.columns:
        features["art_unit_group"] = pd.to_numeric(
            augmented["group_art_unit"].astype(str).str[:3], errors="coerce"
        )

    for col in PATENT_NULLABLE_NUMERIC:
        if col not in augmented.columns:
            continue
        values = pd.to_numeric(augmented[col], errors="coerce")
        features[f"{col}_missing"] = values.isna().astype(int)
        features[col] = values

    if "days_since_last_assignment" in augmented.columns:
        features["days_since_last_assignment"] = pd.to_numeric(
            augmented["days_since_last_assignment"], errors="coerce"
        )

    # Regime indicators distinguish missingness causes that a downstream
    # imputer would otherwise collapse: no file wrapper at all vs.
    # wrapper with empty assignmentBag (see
    # docs/features/patent_file_wrapper_features.md §"Missingness semantics").
    if "n_assignments_pre_t0" in augmented.columns:
        features["no_recorded_assignment"] = (
            pd.to_numeric(augmented["n_assignments_pre_t0"], errors="coerce").fillna(0) == 0
        ).astype(int)
    if "n_events_pre_t0" in augmented.columns:
        features["patent_features_missing"] = (
            pd.to_numeric(augmented["n_events_pre_t0"], errors="coerce").isna().astype(int)
        )

    # Counts: NaN ⇒ no file wrapper at all, which is row-local and
    # safe to fill with 0 (the regime is captured by
    # `patent_features_missing` above).
    for col in PATENT_COUNT_FEATURES:
        if col in augmented.columns:
            features[col] = (
                pd.to_numeric(augmented[col], errors="coerce").fillna(0).astype(int)
            )

    # Raw categoricals — encoded downstream by the modeling preprocessor
    # so the encoder is fit on training rows only. Plain object dtype
    # with NaN for missing keeps sklearn's encoders happy.
    for col in (*OHE_CATEGORICAL_COLUMNS, *FREQUENCY_CATEGORICAL_COLUMNS):
        if col in augmented.columns:
            values = augmented[col].astype(object)
            features[col] = values.where(values.notna(), np.nan)

    # Tier A petition-text features. The joiner attaches `petition_text`
    # from `Frame.PETITION_TEXTS`; rows with no cached text get NaN, so
    # the regex aggregator falls back to 0 per its empty-text branch.
    if "petition_text" in augmented.columns:
        text_features = pd.DataFrame(
            list(augmented["petition_text"].map(_aggregate_petition_text_row)),
            index=augmented.index,
        )
        for col in PETITION_TEXT_FEATURE_KEYS:
            features[col] = text_features[col]

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features


# ---------------------------------------------------------------------------
# Tier A petition-text features
#
# Pure per-row transforms invoked from `build_features` once the joined
# frame carries the `petition_text` column (left-joined by the joiner
# from `Frame.PETITION_TEXTS`). Same shape as patent aggregation.
# ---------------------------------------------------------------------------


def _aggregate_petition_text_row(text: object) -> dict[str, int]:
    """Run the Tier A regex set over one petition's pdfplumber-extracted text.

    Mirrors `_aggregate_patent_row`: row-local, no I/O, NaN/None text
    treated as empty (every feature falls back to 0). Booleans are
    cast to 0/1 so the result drops straight into a numeric DataFrame
    column.
    """
    if not isinstance(text, str) or not text:
        return {k: 0 for k in PETITION_TEXT_FEATURE_KEYS}

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
        _FINTIV_FACTOR_WORD_TO_INDEX[m.group(1).lower()]
        for m in PETITION_FINTIV_FACTOR_ORDINAL_PATTERN.finditer(text)
    })
    n_heading_hits = len({m.group(1) for m in PETITION_FINTIV_FACTOR_HEADING_PATTERN.finditer(text)})
    # Three substring checks beat a full text.lower() at corpus scale —
    # petitions are 100-500KB and a lower() copy each call is wasteful
    # when only one literal needs case-insensitive matching.
    fintiv_anywhere = "Fintiv" in text or "fintiv" in text or "FINTIV" in text
    n_wordnum_hits = (
        len({
            _FINTIV_FACTOR_WORD_TO_INDEX[m.group(1).lower()]
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


__all__ = ["build_features"]
