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
    MIN_PETITION_TEXT_CHARS,
    OHE_CATEGORICAL_COLUMNS,
    PATENT_COUNT_FEATURES,
    PATENT_NULLABLE_NUMERIC,
    PETITION_TEXT_FEATURE_KEYS,
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

    Trials whose `petition_text` is too short for Tier A regex extraction
    (cache miss, BLANK sentinel, all-whitespace, cover-page-only partial)
    are dropped here per `_select_usable_rows`; the returned frame's
    `trial_number` column is the join key for downstream label
    attachment.

    Args:
        df: Joined frame from `parse.joiner.join_all`.

    Returns:
        Feature frame, `trial_number`-keyed, with calendar/row-local
        numerics (`filing_year`, `filing_month`, `art_unit_group`);
        T₀-filtered patent aggregates (`n_*_pre_t0`, `n_office_actions`,
        `n_distinct_assignees_pre_t0`, `n_parent_applications`);
        nullable scalars (`prosecution_span_days`,
        `days_grant_to_petition`, `days_since_last_assignment`) paired
        with their `_missing` regime indicators set *before* any
        imputation; raw categoricals for the downstream encoder
        (`technology_center`, `cpc_section`,
        `petitioner_real_party`, `owner_real_party`). The label column
        (`cancelled`) is not part of the returned frame — callers join
        it back via `trial_number`.
    """
    df = _select_usable_rows(df)
    patent_features = _aggregate_patents(df)
    augmented = pd.concat(
        [df.reset_index(drop=True), patent_features.reset_index(drop=True)],
        axis=1,
    )

    features = pd.DataFrame(index=augmented.index)

    # `trial_number` is the join key for downstream label / audit
    # attachment. After `_select_usable_rows` the row count no longer
    # matches the input joined frame, so positional alignment is unsafe;
    # callers must merge on `trial_number`.
    if "trial_number" in augmented.columns:
        features["trial_number"] = augmented["trial_number"].astype(str)

    if "petition_filing_date" in augmented.columns:
        pf = pd.to_datetime(augmented["petition_filing_date"], errors="coerce")
        features["filing_year"] = pf.dt.year.astype("Int64")
        # Month captures intra-year seasonality the year column can't:
        # §315(b) one-year-from-complaint bunching, USPTO fiscal-year
        # boundary (Sept 30), holiday slowdowns, and Director-memo timing
        # within a year (e.g. Vidal memo June 2022 splits 2022 into a
        # pre/post-Fintiv-relaxation half).
        features["filing_month"] = pf.dt.month.astype("Int64")

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

    # Tier A petition-text features. Callers are expected to have run
    # `select_usable_rows` upstream so every surviving row carries a
    # real petition_text body — no missing-data branch here.
    if "petition_text" in augmented.columns:
        text_features = pd.DataFrame(
            list(augmented["petition_text"].map(_aggregate_petition_text_row)),
            index=augmented.index,
        )
        for col in PETITION_TEXT_FEATURE_KEYS:
            features[col] = text_features[col]

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features


def _select_usable_rows(joined: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose `petition_text` is too short for Tier A regex extraction.

    Internal to `build_features` — the petition-text length threshold
    is a feature-pipeline implementation detail, not a driver concern.

    `_aggregate_petition_text_row` requires a real petition body to
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
    documented policy (`docs/features/features_csv_dictionary.md` §7) —
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


# ---------------------------------------------------------------------------
# Tier A petition-text features
#
# Pure per-row transforms invoked from `build_features` once the joined
# frame carries the `petition_text` column (left-joined by the joiner
# from `Frame.PETITION_TEXTS`). Same shape as patent aggregation.
# ---------------------------------------------------------------------------


def _aggregate_petition_text_row(text: str) -> dict[str, int]:
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
