"""Shared utilities for the parse subpackage."""

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
