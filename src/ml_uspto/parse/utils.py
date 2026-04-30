"""Shared utilities for the parse subpackage.

Cross-module helpers that don't fit any single parse module — date
coercion today, more as the subpackage grows. Things scoped to one
module belong in that module, not here.
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
