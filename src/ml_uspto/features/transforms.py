"""Joined frame → leakage-free intermediate feature matrix. See `docs/engineering/features/`."""

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
    PTAB_ERAS,
    TRIAL_EVENT_PREFIXES,
)
from ml_uspto.features.schemas.enums import NON_TRIAL_CATEGORIES, EventCategory, InventorGeo
from ml_uspto.features.schemas.patterns import NORMALIZE_NON_ALNUM
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
from ml_uspto.utils import as_list, to_date

logger = logging.getLogger(__name__)


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
    # Substring beats text.lower() at corpus scale; word-form / bare-line variants gate on this to avoid Graham/KSR FPs.
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
        # Effort proxy; floored at MIN_PETITION_TEXT_CHARS by the upstream length filter (~5K–860K on cohort).
        "petition_text_length": len(text),
    }


def _aggregate_patent_row(row: pd.Series) -> dict[str, object]:
    """T₀ leakage filter + count/span aggregation for one joined-frame row."""
    counts: dict[EventCategory, int] = {cat: 0 for cat in NON_TRIAL_CATEGORIES}

    t0 = to_date(row.get("petition_filing_date"))
    if t0 is None:
        return {
            "n_events": 0,
            "prosecution_span_days": None,
            **{f"n_{cat.value.lower()}": 0 for cat in NON_TRIAL_CATEGORIES},
            "n_assignments": 0,
            "n_distinct_assignees": 0,
            "days_since_last_assignment": None,
            "days_grant_to_petition": None,
            "n_parent_applications": 0,
            "cpc_section": None,
            "n_cpc_codes": 0,
            "n_cpc_subclasses": 0,
            "no_recorded_assignment": 1,
            "inventor_geo": None,
        }

    kept_event_dates: list[date] = []
    for code, raw_d in zip(
        as_list(row.get("event_codes")),
        as_list(row.get("event_dates")),
        strict=False,
    ):
        d = to_date(raw_d)
        if d is None or d >= t0:
            continue
        code_str = str(code or "").strip()
        if not code_str:
            continue
        cat = (
            EventCategory.TRIAL
            if any(code_str.startswith(prefix) for prefix in TRIAL_EVENT_PREFIXES)
            else EVENT_CODE_CATEGORIES.get(code_str, EventCategory.OTHER)
        )
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
        as_list(row.get("assignment_received_dates")),
        as_list(row.get("assignment_recorded_dates")),
        as_list(row.get("assignees_per_assignment")),
        strict=False,
    ):
        candidates = [d for d in (to_date(r), to_date(c)) if d is not None and d < t0]
        if not candidates:
            continue
        pre_t0_assignment_dates.append(min(candidates))
        for name in as_list(names):
            if isinstance(name, str) and name:
                normalized = " ".join(
                    NORMALIZE_NON_ALNUM.sub(" ", name.lower()).strip().split()
                )
                if normalized:
                    distinct_assignees.add(normalized)

    days_since_last_assignment = (
        (t0 - max(pre_t0_assignment_dates)).days if pre_t0_assignment_dates else None
    )
    grant = to_date(row.get("grant_date"))
    days_grant_to_petition = (t0 - grant).days if grant is not None else None

    cpc_codes = [c for c in as_list(row.get("cpc_codes")) if isinstance(c, str) and c]
    cpc_section = next(
        (c[0].upper() for c in cpc_codes if c[0].isalpha()), None
    )

    countries = as_list(row.get("inventor_country_codes"))
    if not countries:
        inventor_geo = None
    elif all(c == "US" for c in countries):
        inventor_geo = InventorGeo.US_ONLY.value
    else:
        inventor_geo = InventorGeo.ANY_FOREIGN.value

    return {
        "n_events": sum(counts.values()),
        "prosecution_span_days": prosecution_span_days,
        **{f"n_{cat.value.lower()}": n for cat, n in counts.items()},
        "n_assignments": len(pre_t0_assignment_dates),
        "n_distinct_assignees": len(distinct_assignees),
        "days_since_last_assignment": days_since_last_assignment,
        "days_grant_to_petition": days_grant_to_petition,
        "n_parent_applications": len(as_list(row.get("parent_app_numbers"))),
        "cpc_section": cpc_section,
        "n_cpc_codes": len(cpc_codes),
        "n_cpc_subclasses": len({c[:4] for c in cpc_codes}),
        "no_recorded_assignment": int(not pre_t0_assignment_dates),
        "inventor_geo": inventor_geo,
    }


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Joined frame → leakage-free intermediate feature matrix.

    Drops trials with no usable petition text or no matched patent file
    wrapper, then assembles temporal, patent-side, categorical, and Tier A
    text features. All values are observable at petition filing — no field
    leaks information past T₀.

    Args:
        df: Joined trial frame (`Frame.JOINED_TRIALS`). Required columns:
            `trial_number`, `petition_filing_date`, `petition_text`,
            `application_number`, plus the columns named in
            `PATENT_NULLABLE_NUMERIC`, `PATENT_COUNT_FEATURES`,
            `OHE_CATEGORICAL_COLUMNS`, and `FREQUENCY_CATEGORICAL_COLUMNS`.

    Returns:
        `trial_number`-keyed feature matrix. Categoricals are passed
        through as raw strings; the modeling preprocessor fits encoders
        train-only. The label (`cancelled`) is attached downstream.

    Raises:
        KeyError: A required column is missing from `df`.
    """
    # Drop unusable rows: short text (cache miss / BLANK sentinel / cover-page-only)
    # and missing wrapper (fill-with-0 would conflate "not fetched" with "zero events").
    text = df["petition_text"]
    str_mask = text.apply(lambda v: isinstance(v, str))
    long_enough = str_mask & (text.fillna("").str.len() >= MIN_PETITION_TEXT_CHARS)
    n_short = int((~long_enough).sum())
    if n_short:
        logger.info(
            "Dropped %d/%d trials (%.2f%%) with petition_text < %d chars",
            n_short, len(df), n_short / max(len(df), 1) * 100, MIN_PETITION_TEXT_CHARS,
        )
    df = df.loc[long_enough].copy()

    has_wrapper = df["application_number"].notna()
    n_dropped = int((~has_wrapper).sum())
    if n_dropped:
        logger.info(
            "Dropped %d/%d trials (%.2f%%) with no patent file wrapper",
            n_dropped, len(df), n_dropped / max(len(df), 1) * 100,
        )
    df = df.loc[has_wrapper].copy()

    # Augment with row-local patent-side aggregation + PTAB-era assignment.
    # Era intervals from `config/ptab_eras.yaml` (left-closed/right-open).
    patent_features = pd.DataFrame(
        [_aggregate_patent_row(row) for _, row in df.iterrows()],
        index=df.index,
    )
    augmented = pd.concat([df, patent_features], axis=1)

    pf = pd.to_datetime(augmented["petition_filing_date"], errors="coerce")
    era_starts = pd.to_datetime([s for _, s in PTAB_ERAS]).to_numpy()
    era_names = np.array([n for n, _ in PTAB_ERAS], dtype=object)
    era_idx = np.searchsorted(era_starts, pf.to_numpy(), side="right") - 1
    augmented["ptab_era"] = np.where(
        pf.isna().to_numpy() | (era_idx < 0),
        None,
        era_names[np.clip(era_idx, 0, len(era_names) - 1)],
    )

    # Build the output matrix. Downstream merges on `trial_number`.
    features = pd.DataFrame(index=augmented.index)
    features["trial_number"] = augmented["trial_number"].astype(str)

    # Temporal — `filing_month` captures §315(b) bunching, fiscal-year edges, Director-memo timing.
    features["filing_year"] = pf.dt.year.astype("Int64")
    features["filing_month"] = pf.dt.month.astype("Int64")
    features["art_unit_group"] = pd.to_numeric(
        augmented["group_art_unit"].astype(str).str[:3], errors="coerce"
    )

    # Patent-side numeric. `<col>_missing` paired *before* imputation so NaN signal survives.
    for col in PATENT_NULLABLE_NUMERIC:
        values = pd.to_numeric(augmented[col], errors="coerce")
        features[f"{col}_missing"] = values.isna().astype(int)
        features[col] = values
    features["days_since_last_assignment"] = augmented["days_since_last_assignment"]
    features["no_recorded_assignment"] = augmented["no_recorded_assignment"].astype(int)
    for col in PATENT_COUNT_FEATURES:
        features[col] = augmented[col].astype(int)

    # Categoricals — raw pass-through; encoder fit train-only by the modeling preprocessor.
    for col in (*OHE_CATEGORICAL_COLUMNS, *FREQUENCY_CATEGORICAL_COLUMNS):
        features[col] = augmented[col].astype(object)

    # Tier A petition-text features.
    text_features = pd.DataFrame(
        augmented["petition_text"].map(aggregate_petition_text_row).tolist(),
        index=augmented.index,
    )
    features[list(PETITION_TEXT_FEATURE_KEYS)] = text_features[list(PETITION_TEXT_FEATURE_KEYS)]

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features


__all__ = ["build_features"]
