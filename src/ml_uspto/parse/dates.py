"""Date coercion utilities for the parse subpackage.

Centralizes the heterogeneous-input → `date | None` coercion used when
reading raw API payloads, where a field can arrive as a string, a
pandas timestamp, a Python `date`/`datetime`, or NaN / None.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd


def to_date(value: Any) -> date | None:
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
