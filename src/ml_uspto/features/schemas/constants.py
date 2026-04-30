"""Subpackage-local constants for `ml_uspto.features.transforms`.

These are mechanical column lists (which patent-aggregator columns to
consume, and which carry semantic NaN) — not a domain-revisable
taxonomy, but per CLAUDE.md they still belong in a single import site
rather than at the use site in `transforms.py`. Adjust here if the
feature set changes.
"""

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


__all__ = ["PATENT_COUNT_FEATURES", "PATENT_NULLABLE_NUMERIC"]
