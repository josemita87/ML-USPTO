"""Build the binary `cancelled` label per `docs/scope/prediction_scope.md` §3.

Target = 1 iff the trial's *terminating* Final Written Decision held all
challenged claims unpatentable. Everything else (institution denied,
discretionary denial, settled, procedurally terminated, FWD where any claim
survived) is 0. Trials still pending are excluded entirely.

The terminating FWD is identified per trial as the latest `decisionIssueDate`
among rows whose `decision_type` indicates a Final Written Decision (this
correctly handles remand: a remand FWD wins over the vacated original).
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# trialStatusCategory values that resolve directly to label-0 without
# inspecting the decision side. Sourced from §3 table in prediction_scope.md.
NON_FWD_LABEL_0_STATUSES = {
    "Institution Denied",
    "Discretionary Denial",
    "Terminated-Settled",
    "Terminated",  # procedural — joinder, improper filing, etc.
}

# trialStatusCategory values that mean the trial is still in progress —
# we don't have a terminating outcome yet, so exclude.
PENDING_STATUSES = {
    "Trial Instituted",
    # Add other in-progress states here as the data surfaces them.
}

# decisionTypeCategory substrings that identify a Final Written Decision row.
# Matches both the original FWD and any remand FWD ("Final Written Decision
# On CAFC Remand" was observed in IPR2022-01002).
FWD_DECISION_TYPE_MARKER = "Final Written Decision"

# trialOutcomeCategory values from the *terminating* FWD that map to label 1.
# Verified values should be added as the corpus is explored. These strings
# come from the USPTO ODP `trialOutcomeCategory` field on `decisionData`.
ALL_CLAIMS_UNPATENTABLE_OUTCOMES = {
    "All Challenged Claims Unpatentable",
    # Add any equivalent phrasings discovered from real data here.
}


def _identify_terminating_fwd(decisions: pd.DataFrame) -> pd.DataFrame:
    """Return one row per trial: the FWD with the latest decision_issue_date."""
    fwds = decisions[
        decisions["decision_type"]
        .fillna("")
        .str.contains(FWD_DECISION_TYPE_MARKER, case=False, regex=False)
    ].copy()
    if fwds.empty:
        return fwds.assign(terminating_outcome=pd.Series(dtype="object"))

    fwds["decision_issue_date"] = pd.to_datetime(
        fwds["decision_issue_date"], errors="coerce"
    )
    idx = fwds.groupby("trial_number")["decision_issue_date"].idxmax()
    terminating = fwds.loc[idx, ["trial_number", "trial_outcome"]].rename(
        columns={"trial_outcome": "terminating_outcome"}
    )
    return terminating


def preprocess(
    proceedings: pd.DataFrame, decisions: pd.DataFrame
) -> pd.DataFrame:
    """Join proceedings + decisions, derive the binary `cancelled` label.

    Returns one row per trial with the original proceedings columns plus:
        - terminating_outcome: trialOutcomeCategory of the terminating FWD
          (None for trials that ended before reaching FWD).
        - cancelled: 1 if FWD held all claims unpatentable, else 0.
    Pending trials are dropped.
    """
    logger.info("Raw proceedings: %d", len(proceedings))

    df = proceedings[proceedings["trial_type"] == "IPR"].copy()
    logger.info("After filtering to IPR: %d", len(df))

    df = df[~df["trial_status"].isin(PENDING_STATUSES)].copy()
    logger.info("After dropping pending: %d", len(df))

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

    terminating = _identify_terminating_fwd(decisions)
    df = df.merge(terminating, on="trial_number", how="left")

    label_zero_by_status = df["trial_status"].isin(NON_FWD_LABEL_0_STATUSES)
    label_one_by_outcome = df["terminating_outcome"].isin(
        ALL_CLAIMS_UNPATENTABLE_OUTCOMES
    )

    df["cancelled"] = label_one_by_outcome.astype(int)
    # Status-based label-0 trials can't be label-1, but we keep the assignment
    # explicit so a future broadening of the outcome set can't silently leak.
    df.loc[label_zero_by_status, "cancelled"] = 0

    df = df.dropna(subset=["petition_filing_date", "patent_number"])
    df["technology_center"] = df["technology_center"].astype(str).str.strip()

    logger.info(
        "Final dataset: %d rows (%.1f%% cancelled)",
        len(df),
        df["cancelled"].mean() * 100 if len(df) else 0.0,
    )
    return df
