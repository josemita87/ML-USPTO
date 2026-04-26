"""Feature engineering for IPR cancellation prediction (binary target).

Every transform here must be observable at T₀ (`petition_filing_date`) per
`docs/scope/prediction_scope.md` §4. Features that aggregate over other
trials' outcomes (e.g., per-tech-center base rates) require a strict
temporal cutoff and are therefore omitted from this baseline — a tree model
recovers per-category rates from the one-hot dummies on its own at the
cardinality of `technology_center`.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create model-ready features from preprocessed data."""
    features = pd.DataFrame(index=df.index)

    # --- Date-based features ---
    if "petition_filing_date" in df.columns:
        features["filing_year"] = df["petition_filing_date"].dt.year
        features["filing_month"] = df["petition_filing_date"].dt.month
        features["filing_dayofweek"] = df["petition_filing_date"].dt.dayofweek

    # Time from patent grant to IPR petition (days)
    if "grant_date" in df.columns and "petition_filing_date" in df.columns:
        delta = (df["petition_filing_date"] - df["grant_date"]).dt.days
        features["days_grant_to_petition"] = delta

    # --- Technology center (categorical) ---
    if "technology_center" in df.columns:
        tc_dummies = pd.get_dummies(
            df["technology_center"], prefix="tc", dtype=int
        )
        features = pd.concat([features, tc_dummies], axis=1)

    # --- Petitioner frequency encoding ---
    if "petitioner_real_party" in df.columns:
        pet_counts = df["petitioner_real_party"].value_counts()
        features["petitioner_frequency"] = (
            df["petitioner_real_party"].map(pet_counts).fillna(0).astype(int)
        )

    # --- Patent owner frequency encoding ---
    if "owner_real_party" in df.columns:
        owner_counts = df["owner_real_party"].value_counts()
        features["owner_frequency"] = (
            df["owner_real_party"].map(owner_counts).fillna(0).astype(int)
        )

    # --- Art unit features ---
    if "group_art_unit" in df.columns:
        # Extract first 3 digits as a broader grouping
        features["art_unit_group"] = (
            df["group_art_unit"]
            .astype(str)
            .str[:3]
            .apply(pd.to_numeric, errors="coerce")
        )

    # Fill NaNs
    features = features.fillna(0)

    logger.info("Built %d features for %d samples", features.shape[1], features.shape[0])
    return features
