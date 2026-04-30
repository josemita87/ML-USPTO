"""Feature engineering for IPR cancellation prediction (binary target).

Every feature here must be observable at T₀ (`petition_filing_date`) per
`docs/scope/prediction_scope.md` §4. T₀ enforcement happens upstream in
`parse.patent_aggregator`; this module just consumes the aggregates.

Missingness handling — see `docs/features/patent_file_wrapper_features.md`
§"Missingness semantics" for the full rationale on the four regimes
(no wrapper / no assignment / legitimate-zero counts / nullable scalars)
and why the regime indicators must be added *before* imputation.
"""

import logging

import pandas as pd

from ml_uspto.features.schemas.constants import (
    PATENT_COUNT_FEATURES,
    PATENT_NULLABLE_NUMERIC,
)

logger = logging.getLogger(__name__)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Produce the model-ready feature matrix from joined trials.

    Input is the `joined_trials.parquet` shape — proceedings columns,
    label, and patent-aggregator columns. Output rows align 1:1 with
    `df`; the label column is *not* part of the returned frame.
    """
    features = pd.DataFrame(index=df.index)

    if "petition_filing_date" in df.columns:
        pf = pd.to_datetime(df["petition_filing_date"], errors="coerce")
        features["filing_year"] = pf.dt.year
        features["filing_month"] = pf.dt.month
        features["filing_dayofweek"] = pf.dt.dayofweek

    if "technology_center" in df.columns:
        tc = df["technology_center"].astype(str).str.strip()
        features = pd.concat([features, pd.get_dummies(tc, prefix="tc", dtype=int)], axis=1)

    if "cpc_section" in df.columns:
        features = pd.concat(
            [features, pd.get_dummies(df["cpc_section"], prefix="cpc", dtype=int, dummy_na=True)],
            axis=1,
        )

    if "petitioner_real_party" in df.columns:
        pet_counts = df["petitioner_real_party"].value_counts()
        features["petitioner_frequency"] = (
            df["petitioner_real_party"].map(pet_counts).fillna(0).astype(int)
        )
    if "owner_real_party" in df.columns:
        owner_counts = df["owner_real_party"].value_counts()
        features["owner_frequency"] = (
            df["owner_real_party"].map(owner_counts).fillna(0).astype(int)
        )

    if "group_art_unit" in df.columns:
        features["art_unit_group"] = (
            df["group_art_unit"].astype(str).str[:3]
            .apply(pd.to_numeric, errors="coerce")
        )

    for col in PATENT_COUNT_FEATURES:
        if col in df.columns:
            features[col] = pd.to_numeric(df[col], errors="coerce")

    for col in PATENT_NULLABLE_NUMERIC:
        if col not in df.columns:
            continue
        values = pd.to_numeric(df[col], errors="coerce")
        features[f"{col}_missing"] = values.isna().astype(int)
        features[col] = values

    if "days_since_last_assignment" in df.columns:
        features["days_since_last_assignment"] = pd.to_numeric(
            df["days_since_last_assignment"], errors="coerce"
        )

    # Regime indicators: trials with no file wrapper at all vs. trials
    # whose wrapper has an empty assignmentBag — different semantics
    # (see docs/features/patent_file_wrapper_features.md §"Missingness").
    if "n_assignments_pre_t0" in df.columns:
        features["no_recorded_assignment"] = (
            pd.to_numeric(df["n_assignments_pre_t0"], errors="coerce").fillna(0) == 0
        ).astype(int)
    if "n_events_pre_t0" in df.columns:
        features["patent_features_missing"] = (
            pd.to_numeric(df["n_events_pre_t0"], errors="coerce").isna().astype(int)
        )

    # 0 events == empty bag, so 0-fill the counts (regime indicators
    # above carry the "no wrapper" signal that's lost by this fill).
    for col in PATENT_COUNT_FEATURES:
        if col in features.columns:
            features[col] = features[col].fillna(0).astype(int)

    for col in (*PATENT_NULLABLE_NUMERIC, "days_since_last_assignment"):
        if col in features.columns:
            features[col] = features[col].fillna(features[col].median())

    features = features.fillna(0)

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features
