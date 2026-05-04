"""Subpackage-local constants for `ml_uspto.features`."""

import datetime as dt
from functools import lru_cache

import yaml

from ml_uspto import paths
from ml_uspto.features.schemas.enums import EventCategory

# Pre-T₀ count features (post `select_with_file_wrapper`, so 0 means
# "wrapper present, no events" — never "wrapper missing").
PATENT_COUNT_FEATURES: tuple[str, ...] = (
    "n_events",
    "n_assignments",
    "n_distinct_assignees",
    "n_parent_applications",
    "n_pe",
    "n_ex",
    "n_aa",
    "n_ad",
    "n_iss",
    "n_maint",
    "n_other",
)

# Patent features whose NaN ≠ 0 — each gets a paired `<name>_missing`
# indicator. `days_since_last_assignment` is absent because it would
# duplicate `no_recorded_assignment` (collinear with `n_assignments == 0`).
PATENT_NULLABLE_NUMERIC: tuple[str, ...] = (
    "prosecution_span_days",
    "days_grant_to_petition",
)

# Closed taxonomies; OHE column set frozen at train-fit time.
OHE_CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "technology_center",
    "cpc_section",
    "ptab_era",
)

# Open-vocab party identifiers; frequencies refit per CV fold.
FREQUENCY_CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "petitioner_real_party",
    "owner_real_party",
)

# Empirical 2026-05-02 6,366-trial cohort: failures ≤3,040 chars,
# real petitions ≥59K. 5K is the safe cut.
MIN_PETITION_TEXT_CHARS: int = 5_000

PETITION_TEXT_FEATURE_KEYS: tuple[str, ...] = (
    "n_grounds",
    "n_grounds_102",
    "n_grounds_103",
    "has_sotera_stipulation",
    "mentions_fintiv_factors",
)


@lru_cache(maxsize=1)
def _load_patent_event_codes() -> dict:
    with open(paths.PATENT_EVENT_CODES_YAML) as f:
        return yaml.safe_load(f)


_event_cfg = _load_patent_event_codes()

EVENT_CODE_CATEGORIES: dict[str, EventCategory] = {
    code: EventCategory(category) for code, category in _event_cfg["codes"].items()
}
BANNED_EVENT_CATEGORIES: frozenset[EventCategory] = frozenset(
    EventCategory(category) for category in _event_cfg["banned_categories"]
)
TRIAL_EVENT_PREFIXES: tuple[str, ...] = tuple(_event_cfg.get("banned_prefixes", []))


@lru_cache(maxsize=1)
def _load_ptab_eras() -> dict:
    with open(paths.PTAB_ERAS_YAML) as f:
        return yaml.safe_load(f)


# Era-name → start-date (inclusive). Eras are left-closed, right-open
# intervals running until the next era's start; the last era runs to
# today. Sorted by start date so the era lookup can binary-search.
PTAB_ERAS: tuple[tuple[str, dt.date], ...] = tuple(
    sorted(
        ((name, dt.date.fromisoformat(start)) for name, start in _load_ptab_eras()["eras"].items()),
        key=lambda kv: kv[1],
    )
)


__all__ = [
    "PATENT_COUNT_FEATURES",
    "PATENT_NULLABLE_NUMERIC",
    "OHE_CATEGORICAL_COLUMNS",
    "FREQUENCY_CATEGORICAL_COLUMNS",
    "MIN_PETITION_TEXT_CHARS",
    "PETITION_TEXT_FEATURE_KEYS",
    "EVENT_CODE_CATEGORIES",
    "BANNED_EVENT_CATEGORIES",
    "TRIAL_EVENT_PREFIXES",
    "PTAB_ERAS",
]
