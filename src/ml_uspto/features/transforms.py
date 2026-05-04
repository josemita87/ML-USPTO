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
    PTAB_ERAS,
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

    # PTAB Director era at petition filing — left-closed/right-open
    # intervals from `config/ptab_eras.yaml`. Stashed on `augmented` so
    # the OHE loop below picks it up like any other categorical.
    era_starts = pd.to_datetime([s for _, s in PTAB_ERAS]).to_numpy()
    era_names = np.array([n for n, _ in PTAB_ERAS], dtype=object)
    era_idx = np.searchsorted(era_starts, pf.to_numpy(), side="right") - 1
    era_idx_clipped = np.clip(era_idx, 0, len(era_names) - 1)
    augmented["ptab_era"] = np.where(
        pf.isna().to_numpy() | (era_idx < 0),
        None,
        era_names[era_idx_clipped],
    )

    # Collapse high-cardinality `inventor_country_codes` to a 3-way bucket
    # {us_only, any_foreign, NaN}; NaN survives the OHE pass-through as its
    # own level via MISSING_CATEGORY_SENTINEL downstream.
    def _inventor_geo(arr):
        if arr is None or (hasattr(arr, "__len__") and len(arr) == 0):
            return None
        return "us_only" if all(c == "US" for c in arr) else "any_foreign"

    augmented["inventor_geo"] = augmented["inventor_country_codes"].map(_inventor_geo)

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

    # Classification breadth: total CPC codes + distinct subclass count
    # (first 4 chars, e.g. `H01Q`, `A61N`).
    def _count_cpc(arr):
        if arr is None or (hasattr(arr, "__len__") and len(arr) == 0):
            return 0, 0
        codes = [c for c in arr if isinstance(c, str) and c]
        return len(codes), len({c[:4] for c in codes})

    cpc_pairs = augmented["cpc_codes"].map(_count_cpc).tolist()
    features["n_cpc_codes"], features["n_cpc_subclasses"] = (
        zip(*cpc_pairs) if cpc_pairs else ([], [])
    )

    # Raw pass-through; encoder is fit train-only by the modeling-side preprocessor.
    for col in (*OHE_CATEGORICAL_COLUMNS, *FREQUENCY_CATEGORICAL_COLUMNS):
        values = augmented[col].astype(object)
        features[col] = values.where(values.notna(), np.nan)

    text_features = pd.DataFrame(
        augmented["petition_text"].map(aggregate_petition_text_row).tolist(),
        index=augmented.index,
    )
    features[list(PETITION_TEXT_FEATURE_KEYS)] = text_features[list(PETITION_TEXT_FEATURE_KEYS)]

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features


__all__ = ["build_features"]
