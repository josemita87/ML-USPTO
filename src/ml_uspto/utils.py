"""Project-wide general-purpose helpers.

Subpackage-local helpers belong in their own module under that
subpackage. Only utilities used across two or more subpackages live
here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd


def to_date(value: Any) -> date | None:
    """Coerce a heterogeneous date-like value to `date`, returning None on failure."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.Timestamp(value).date()
    except (ValueError, TypeError):
        return None


def as_list(value: object) -> list[Any]:
    """Coerce a heterogeneous value (None / scalar NaN / sequence / array-like) to a list.

    Tolerant of the shapes that fall out of pandas row access on
    parallel-array columns: missing → `[]`, list/tuple → list copy,
    numpy/pyarrow array-like (anything with `.tolist`) → list, scalar
    NaN → `[]`. Anything else also collapses to `[]` rather than
    raising — callers iterate the result and treat empty as "no data".
    """
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
