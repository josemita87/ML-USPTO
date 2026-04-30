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


class QuarantineReason(StrEnum):
    """Why a trial failed petition assembly. Emitted in `QuarantineEntry.reason`."""

    PICKER_NO_MATCH = "picker_no_match"  # picker ran but rejected every candidate
    NO_DOCUMENTS = "no_documents"  # trial absent from documents/search corpus


class PatentQuarantineReason(StrEnum):
    """Why an application file wrapper could not be fetched or parsed."""

    NOT_FOUND = "not_found"
    HTTP_ERROR = "http_error"
    REQUEST_ERROR = "request_error"
    EMPTY_RESPONSE = "empty_response"


class DecisionPdfFailureReason(StrEnum):
    """Why an FWD PDF download failed. Persisted in `Frame.DECISION_PDF_FAILURES`
    rows so the gap-detector can quarantine retryably (see
    `parse.decisions._recently_failed`)."""

    HTTP_ERROR = "http_error"
    REQUEST_ERROR = "request_error"
    EMPTY_RESPONSE = "empty_response"


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
    PETITION_QUARANTINE = "petition_quarantine"
    PATENTS = "patents"
    PATENT_QUARANTINE = "patent_quarantine"
    DECISION_PDF_FAILURES = "decision_pdf_failures"
    JOINED_TRIALS = "joined_trials"


class DocumentCategory(StrEnum):
    """Subset of USPTO ODP `documentData.documentCategory` values referenced
    in code (scan filter, exhibit drop). Values are API-canonical casing.

    The full set on the documents endpoint is ~21 values (PETITION, Paper,
    OTHER, NOTICE, MOTION, ORDER, RESPONSE, DECISION, FINAL, REPLY, POPR,
    SURREPLY, ADVRSJUDG, …); add members as code starts to switch on them.
    """

    PETITION = "PETITION"
    PAPER = "Paper"
    EXHIBIT = "Exhibit"
    EXHIBITS = "Exhibits"
