"""Subpackage-local constants for `ml_uspto.features`."""

from functools import lru_cache

import yaml

from ml_uspto import paths
from ml_uspto.features.schemas.enums import EventCategory

# Pre-T₀ count features — NaN ⇒ no file wrapper; safe to fill with 0
# (0 events is what an empty bag would have produced upstream).
PATENT_COUNT_FEATURES: tuple[str, ...] = (
    "n_events_pre_t0",
    "n_office_actions",
    "n_ids_filings",
    "n_assignments_pre_t0",
    "n_distinct_assignees_pre_t0",
    "n_parent_applications",
    "n_pe_pre_t0",
    "n_ex_pre_t0",
    "n_aa_pre_t0",
    "n_ad_pre_t0",
    "n_iss_pre_t0",
    "n_maint_pre_t0",
    "n_other_pre_t0",
)

# Patent features whose NaN carries a distinct meaning from 0 — each
# gets a paired `<name>_missing` indicator and median-filled value.
# See docs/features/patent_file_wrapper_features.md §"Missingness semantics".
#
# `days_since_last_assignment` is intentionally absent: its missingness
# is exactly `n_assignments_pre_t0 == 0`, surfaced as the
# `no_recorded_assignment` regime indicator in `transforms.build_features`.
# Adding `days_since_last_assignment_missing` here would emit a
# perfectly collinear duplicate column.
PATENT_NULLABLE_NUMERIC: tuple[str, ...] = (
    "prosecution_span_days",
    "days_grant_to_petition",
)

# Raw categoricals passed through `transforms.build_features` unencoded.
# Encoding (one-hot / frequency) lives in the modeling-side preprocessor
# so it is fit on training rows only — see
# `ml_uspto.models.preprocessing.build_preprocessor`.
#
# OHE: closed taxonomies — TC has 17 stable USPTO codes, CPC section is
# the 9 single-letter classes + nan. `handle_unknown="ignore"` keeps the
# column set frozen at train fit time.
OHE_CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "technology_center",
    "cpc_section",
)

# Frequency: open-vocabulary party identifiers. Counts must be learned
# on train rows only — refit per CV fold via the Pipeline.
FREQUENCY_CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "petitioner_real_party",
    "owner_real_party",
)

# Minimum length (in characters) for a `petition_text` blob to count
# as usable input for the Tier A regex feature aggregator. Empirically
# tuned against the 6,366-trial cohort (2026-05-02): unusable rows
# (literal "BLANK" sentinels written by the ingest driver, all-whitespace
# pdfplumber output, cover-page-only partial extractions) cluster at
# ≤ 3,040 chars; the closest real petition observed sits at p01 = 59,117
# chars, two orders of magnitude above the cut. The 5K threshold is
# safely below any plausible real petition body and above every observed
# extraction failure. Currently catches 20/6,366 trials = 0.31%, under
# the project's <2% drop-policy threshold.
MIN_PETITION_TEXT_CHARS: int = 5_000

# Output-contract column set produced by
# `transforms._aggregate_petition_text_row`. Kept here so the feature
# spec is revisable without touching the aggregation function.
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
]
