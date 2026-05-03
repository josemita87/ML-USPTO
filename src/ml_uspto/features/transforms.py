"""Joined frame → leakage-free intermediate feature matrix.

Orchestrator only. See `docs/engineering/features/`.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ml_uspto.features.patent_aggregator import (
    aggregate_patents,
    select_with_file_wrapper,
)
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
    """Joined frame → leakage-free intermediate feature matrix.

    Treats the joined-frame schema as a contract; missing required
    columns raise. Output is `trial_number`-keyed; the label column
    (`cancelled`) is attached downstream by the caller.
    """
    df = select_usable_rows(df)
    df = select_with_file_wrapper(df)
    patent_features = aggregate_patents(df)
    augmented = pd.concat(
        [df.reset_index(drop=True), patent_features.reset_index(drop=True)],
        axis=1,
    )

    features = pd.DataFrame(index=augmented.index)

    # Usability filters drop rows; positional alignment is unsafe — merge on this.
    features["trial_number"] = augmented["trial_number"].astype(str)

    pf = pd.to_datetime(augmented["petition_filing_date"], errors="coerce")
    features["filing_year"] = pf.dt.year.astype("Int64")
    # Month captures intra-year seasonality year can't: §315(b) bunching,
    # USPTO fiscal-year boundary, Director-memo timing.
    features["filing_month"] = pf.dt.month.astype("Int64")

    features["art_unit_group"] = pd.to_numeric(
        augmented["group_art_unit"].astype(str).str[:3], errors="coerce"
    )

    # Paired `<col>_missing` set *before* imputation so the NaN signal survives.
    for col in PATENT_NULLABLE_NUMERIC:
        values = pd.to_numeric(augmented[col], errors="coerce")
        features[f"{col}_missing"] = values.isna().astype(int)
        features[col] = values

    features["days_since_last_assignment"] = pd.to_numeric(
        augmented["days_since_last_assignment"], errors="coerce"
    )

    features["no_recorded_assignment"] = (augmented["n_assignments"] == 0).astype(int)

    for col in PATENT_COUNT_FEATURES:
        features[col] = augmented[col].astype(int)

    # Raw pass-through; encoder is fit train-only by the modeling-side preprocessor.
    for col in (*OHE_CATEGORICAL_COLUMNS, *FREQUENCY_CATEGORICAL_COLUMNS):
        values = augmented[col].astype(object)
        features[col] = values.where(values.notna(), np.nan)

    text_features = pd.DataFrame(
        list(augmented["petition_text"].map(aggregate_petition_text_row)),
        index=augmented.index,
    )
    for col in PETITION_TEXT_FEATURE_KEYS:
        features[col] = text_features[col]

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features


__all__ = ["build_features"]
