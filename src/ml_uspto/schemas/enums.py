"""String enums for known categorical values in USPTO ODP payloads.

Use sparingly — pydantic models keep these fields as `str | None` to stay
tolerant of new values appearing upstream. Consume the enums where the code
*decides* on a known value (e.g. label construction), not where it merely
passes one through.
"""

from enum import StrEnum


class TrialType(StrEnum):
    IPR = "IPR"
    PGR = "PGR"
    CBM = "CBM"
    DER = "DER"


class TrialStatus(StrEnum):
    """Observed values of `trialMetaData.trialStatusCategory`."""

    INSTITUTION_DENIED = "Institution Denied"
    DISCRETIONARY_DENIAL = "Discretionary Denial"
    TERMINATED_SETTLED = "Terminated-Settled"
    TERMINATED = "Terminated"
    TRIAL_INSTITUTED = "Trial Instituted"


class TrialOutcome(StrEnum):
    """Observed values of `decisionData.trialOutcomeCategory` on FWDs."""

    ALL_CHALLENGED_CLAIMS_UNPATENTABLE = "All Challenged Claims Unpatentable"
