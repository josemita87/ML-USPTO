"""Clean raw proceedings data and derive the target label."""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Statuses where institution was granted (trial proceeded past institution)
INSTITUTED_STATUSES = {
    "Final Written Decision",
    "Final Written Decision - Appealed",
    "Terminated-Settled",
    "Terminated-Adverse Judgment",
    "Trial Instituted",
}

# Statuses where institution was denied
DENIED_STATUSES = {
    "Institution Denied",
}

# All statuses with a clear institution outcome
DECIDED_STATUSES = INSTITUTED_STATUSES | DENIED_STATUSES


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    """Clean raw data and create binary target label."""
    logger.info("Raw records: %d", len(df))

    # Keep only IPR proceedings (API returns PGR/CBM too)
    df = df[df["trial_type"] == "IPR"].copy()
    logger.info("After filtering to IPR only: %d", len(df))

    # Parse dates
    date_cols = [
        "petition_filing_date",
        "accorded_filing_date",
        "institution_decision_date",
        "grant_date",
        "latest_decision_date",
        "termination_date",
    ]
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Keep only cases with a clear institution decision
    df = df[df["trial_status"].isin(DECIDED_STATUSES)].copy()
    logger.info("After filtering to decided cases: %d", len(df))

    # Target: 1 = instituted, 0 = denied
    df["instituted"] = df["trial_status"].isin(INSTITUTED_STATUSES).astype(int)

    # Drop rows missing critical fields
    df = df.dropna(subset=["petition_filing_date", "patent_number"])

    # Normalize technology center to string
    df["technology_center"] = df["technology_center"].astype(str).str.strip()

    logger.info(
        "Final dataset: %d records (%.1f%% instituted)",
        len(df),
        df["instituted"].mean() * 100,
    )
    return df
