"""String enums for known categorical values in USPTO ODP payloads.

Use sparingly — pydantic models keep these fields as `str | None` to stay
tolerant of new values appearing upstream. Consume the enums where the code
*decides* on a known value (e.g. label construction), not where it merely
passes one through.
"""

from enum import StrEnum


class TrialType(StrEnum):
    """PTAB trial types: IPR (inter partes review), PGR (post-grant review), CBM, DER."""

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


class Frame(StrEnum):
    """Stable keys for the project's tabular outputs.

    Each member maps to one persisted DataFrame routed through
    `storage.Storage.{load,save}_frame`. The value is the storage
    key (no extension; the backend chooses the format). Pipeline-stage
    object buckets live in `ingest.schemas.enums.Stage`, not here.
    """

    TRIALS = "trials"
    DECISIONS = "decisions"
    PETITIONS = "petitions"
    PATENTS = "patents"
    JOINED_TRIALS = "joined_trials"
    FEATURES = "features"


class DocumentCategory(StrEnum):
    """Subset of USPTO ODP `documentData.documentCategory` values referenced in code.

    Values use API-canonical casing. Full endpoint set is ~21 values
    (PETITION, Paper, OTHER, NOTICE, MOTION, ORDER, RESPONSE, DECISION,
    FINAL, REPLY, POPR, SURREPLY, ADVRSJUDG, ...); add members as code
    starts to switch on them (scan filter, exhibit drop, etc.).
    """

    PETITION = "PETITION"
    PAPER = "Paper"
    EXHIBIT = "Exhibit"
    EXHIBITS = "Exhibits"
