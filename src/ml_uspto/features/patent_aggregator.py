"""Patent-side T₀ row-local aggregation for the joined frame.

See `docs/engineering/features/patent_file_wrapper_features.md`.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from ml_uspto.features.schemas.constants import (
    BANNED_EVENT_CATEGORIES,
    EVENT_CODE_CATEGORIES,
    TRIAL_EVENT_PREFIXES,
)
from ml_uspto.features.schemas.enums import NON_TRIAL_CATEGORIES, EventCategory
from ml_uspto.features.schemas.patterns import NORMALIZE_NON_ALNUM
from ml_uspto.utils import as_list, to_date

logger = logging.getLogger(__name__)


def select_with_file_wrapper(joined: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose `application_number` did not match any row in `Frame.PATENTS`.

    Mirrors `select_usable_rows` for petition text — fill-with-0 would
    encode "wrapper not fetched" as "patent had zero events".
    """
    if "application_number" not in joined.columns:
        raise KeyError(
            "select_with_file_wrapper requires an 'application_number' column "
            "on the joined frame; patent-side features are mandatory in build_features"
        )
    has_wrapper = joined["application_number"].notna()
    n_dropped = int((~has_wrapper).sum())
    if n_dropped:
        logger.info(
            "Dropped %d/%d trials (%.2f%%) with no patent file wrapper "
            "(application_number unmatched in Frame.PATENTS)",
            n_dropped, len(joined), n_dropped / max(len(joined), 1) * 100,
        )
    return joined.loc[has_wrapper].copy()


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

    cpc_section = next(
        (
            str(code)[0].upper()
            for code in as_list(row.get("cpc_codes"))
            if isinstance(code, str) and code and code[0].isalpha()
        ),
        None,
    )

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
    }


def aggregate_patents(joined: pd.DataFrame) -> pd.DataFrame:
    """Per-row T₀ patent aggregation; returns a frame indexed like `joined`."""
    rows = [_aggregate_patent_row(row) for _, row in joined.iterrows()]
    return pd.DataFrame(rows, index=joined.index)


__all__ = ["aggregate_patents", "select_with_file_wrapper"]
