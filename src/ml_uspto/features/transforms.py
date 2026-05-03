"""Joined frame → leakage-free intermediate feature matrix.

Orchestrator only — domain-specific aggregation lives in sibling
modules:
  - `patent_aggregator.aggregate_patents` for patent-side T₀
    row-local aggregation over `Frame.PATENTS` parallel-array columns;
  - `petition_text.aggregate_petition_text_row` +
    `petition_text.select_usable_rows` for Tier A regex extraction
    over `Frame.PETITION_TEXTS`.

`build_features` assembles their outputs alongside row-local
calendar features, missingness regime indicators, and raw categoricals
for the downstream encoder. Cross-row transforms (one-hot column-set,
frequency counts, median imputation, scaling) are deferred to
`ml_uspto.models.preprocessing.build_preprocessor`, which fits on
training rows only inside a CV-aware Pipeline.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ml_uspto.features.patent_aggregator import aggregate_patents
from ml_uspto.features.petition_text import (
    aggregate_petition_text_row,
    select_usable_rows,
)
from ml_uspto.features.schemas.constants import (
    FREQUENCY_CATEGORICAL_COLUMNS,
    OHE_CATEGORICAL_COLUMNS,
    PATENT_COUNT_FEATURES,
    PATENT_NULLABLE_NUMERIC,
    PETITION_TEXT_FEATURE_KEYS,
)

logger = logging.getLogger(__name__)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Joined frame → leakage-free intermediate matrix.

    Every output column is a function of one row's own raw fields.
    See `docs/engineering/features/features_csv_dictionary.md` for the methodology
    note and `docs/engineering/features/patent_file_wrapper_features.md`
    §"Missingness semantics" for the four-regime missingness model.

    Trials whose `petition_text` is too short for Tier A regex extraction
    (cache miss, BLANK sentinel, all-whitespace, cover-page-only partial)
    are dropped here per `select_usable_rows`; the returned frame's
    `trial_number` column is the join key for downstream label
    attachment.

    Args:
        df: Joined frame from `parse.joiner.join_all`.

    Returns:
        Feature frame, `trial_number`-keyed, with calendar/row-local
        numerics (`filing_year`, `filing_month`, `art_unit_group`);
        T₀-filtered patent aggregates (`n_*_pre_t0`,
        `n_distinct_assignees_pre_t0`, `n_parent_applications`);
        nullable scalars (`prosecution_span_days`,
        `days_grant_to_petition`, `days_since_last_assignment`) paired
        with their `_missing` regime indicators set *before* any
        imputation; raw categoricals for the downstream encoder
        (`technology_center`, `cpc_section`,
        `petitioner_real_party`, `owner_real_party`). The label column
        (`cancelled`) is not part of the returned frame — callers join
        it back via `trial_number`.
    """
    df = select_usable_rows(df)
    patent_features = aggregate_patents(df)
    augmented = pd.concat(
        [df.reset_index(drop=True), patent_features.reset_index(drop=True)],
        axis=1,
    )

    features = pd.DataFrame(index=augmented.index)

    # `trial_number` is the join key for downstream label / audit
    # attachment. After `select_usable_rows` the row count no longer
    # matches the input joined frame, so positional alignment is unsafe;
    # callers must merge on `trial_number`.
    if "trial_number" in augmented.columns:
        features["trial_number"] = augmented["trial_number"].astype(str)

    if "petition_filing_date" in augmented.columns:
        pf = pd.to_datetime(augmented["petition_filing_date"], errors="coerce")
        features["filing_year"] = pf.dt.year.astype("Int64")
        # Month captures intra-year seasonality the year column can't:
        # §315(b) one-year-from-complaint bunching, USPTO fiscal-year
        # boundary (Sept 30), holiday slowdowns, and Director-memo timing
        # within a year (e.g. Vidal memo June 2022 splits 2022 into a
        # pre/post-Fintiv-relaxation half).
        features["filing_month"] = pf.dt.month.astype("Int64")

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

    # `no_recorded_assignment` distinguishes wrapper-with-empty-assignmentBag
    # from wrapper-with-≥1-assignment (see
    # docs/engineering/features/patent_file_wrapper_features.md §"Missingness semantics").
    # The companion "no file wrapper at all" indicator was removed after the
    # 2026-05-03 corpus check confirmed every joined trial has a wrapper —
    # the indicator was vacuous in the v1 build.
    if "n_assignments_pre_t0" in augmented.columns:
        features["no_recorded_assignment"] = (
            pd.to_numeric(augmented["n_assignments_pre_t0"], errors="coerce").fillna(0) == 0
        ).astype(int)

    # Counts: NaN (no file wrapper) treated identically to 0 events.
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

    # Tier A petition-text features. Callers are expected to have run
    # `select_usable_rows` upstream so every surviving row carries a
    # real petition_text body — no missing-data branch here.
    if "petition_text" in augmented.columns:
        text_features = pd.DataFrame(
            list(augmented["petition_text"].map(aggregate_petition_text_row)),
            index=augmented.index,
        )
        for col in PETITION_TEXT_FEATURE_KEYS:
            features[col] = text_features[col]

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features


__all__ = ["build_features"]
