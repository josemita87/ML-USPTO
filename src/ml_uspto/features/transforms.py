"""Joined frame → leakage-free intermediate feature matrix."""

from __future__ import annotations

import logging
import re
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
    TRIAL_EVENT_PREFIXES,
)
from ml_uspto.features.schemas.enums import EventCategory
from ml_uspto.parse.utils import to_date

logger = logging.getLogger(__name__)


_NORMALIZE_NON_ALNUM = re.compile(r"[^a-z0-9]+")

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
    normalized = _NORMALIZE_NON_ALNUM.sub(" ", name.lower()).strip()
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
        (`technology_center`, `cpc_section`, `petitioner_real_party`,
        `owner_real_party`). The label column (`cancelled`) is not
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

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features


__all__ = ["build_features"]
