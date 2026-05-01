"""All Pydantic models for the project, in one place.

Field names mirror the camelCase API payload via Pydantic aliases so models
populate either from raw JSON (`Proceeding.model_validate(payload)`) or from
snake_case kwargs (`Proceeding(trial_number=...)`).
"""

import re
from datetime import date, datetime
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from ml_uspto.schemas.enums import (
    DecisionPdfFailureReason,
)

# ---------------------------------------------------------------------------
# /trials/proceedings
# ---------------------------------------------------------------------------


class TrialMetaData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    trial_type: str | None = Field(default=None, alias="trialTypeCode")
    trial_status: str | None = Field(default=None, alias="trialStatusCategory")
    petition_filing_date: date | None = Field(default=None, alias="petitionFilingDate")
    accorded_filing_date: date | None = Field(default=None, alias="accordedFilingDate")
    institution_decision_date: date | None = Field(default=None, alias="institutionDecisionDate")
    latest_decision_date: date | None = Field(default=None, alias="latestDecisionDate")
    termination_date: date | None = Field(default=None, alias="terminationDate")


class PatentOwnerData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    patent_number: str | None = Field(default=None, alias="patentNumber")
    real_party: str | None = Field(default=None, alias="realPartyInInterestName")
    counsel: str | None = Field(default=None, alias="counselName")
    grant_date: date | None = Field(default=None, alias="grantDate")
    group_art_unit: str | None = Field(default=None, alias="groupArtUnitNumber")
    technology_center: str | None = Field(default=None, alias="technologyCenterNumber")
    inventor_name: str | None = Field(default=None, alias="inventorName")
    application_number: str | None = Field(default=None, alias="applicationNumberText")


class PetitionerData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    real_party: str | None = Field(default=None, alias="realPartyInInterestName")
    counsel: str | None = Field(default=None, alias="counselName")


class Proceeding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    trial_number: str = Field(alias="trialNumber")
    trial_meta_data: TrialMetaData = Field(default_factory=TrialMetaData, alias="trialMetaData")
    patent_owner_data: PatentOwnerData = Field(
        default_factory=PatentOwnerData, alias="patentOwnerData"
    )
    regular_petitioner_data: PetitionerData = Field(
        default_factory=PetitionerData, alias="regularPetitionerData"
    )


# ---------------------------------------------------------------------------
# /trials/decisions and /trials/{id}/documents
# ---------------------------------------------------------------------------


class DocumentData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    document_identifier: str | None = Field(default=None, alias="documentIdentifier")
    document_name: str | None = Field(default=None, alias="documentName")
    document_title: str | None = Field(default=None, alias="documentTitleText")
    document_type: str | None = Field(default=None, alias="documentTypeDescriptionText")
    document_filing_date: date | None = Field(default=None, alias="documentFilingDate")
    filing_party: str | None = Field(default=None, alias="filingPartyCategory")


class DecisionData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    decision_issue_date: date | None = Field(default=None, alias="decisionIssueDate")
    decision_type: str | None = Field(default=None, alias="decisionTypeCategory")
    trial_outcome: str | None = Field(default=None, alias="trialOutcomeCategory")
    statutes_and_rules: list[str] | None = Field(default=None, alias="statuteAndRuleBag")
    issue_types: list[str] | None = Field(default=None, alias="issueTypeBag")


class TrialDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    trial_number: str = Field(alias="trialNumber")
    document_data: DocumentData = Field(default_factory=DocumentData, alias="documentData")
    decision_data: DecisionData | None = Field(default=None, alias="decisionData")


# ---------------------------------------------------------------------------
# Feature-engineering boundary
# ---------------------------------------------------------------------------


class FeatureRow(BaseModel):
    """Mirrors the columns produced by `ml_uspto.features.transforms.build_features`.

    Both training and inference paths construct `FeatureRow` instances; this is
    the single shared codepath that prevents training/serving skew.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str

    filing_year: int | None = None
    filing_month: int | None = None
    filing_dayofweek: int | None = None
    days_grant_to_petition: int | None = None

    petitioner_frequency: int | None = None
    owner_frequency: int | None = None

    art_unit_group: int | None = None
    technology_center: str | None = None

    # Variable-width one-hot for technology_center; persisted as a dict so the
    # contract is stable across training runs even when new TCs appear.
    tech_center_onehot: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Predictions and metrics
# ---------------------------------------------------------------------------


class Prediction(BaseModel):
    """Audit row for the predictions table.

    Target is binary per `docs/scope/prediction_scope.md` §3:
        1 = terminating FWD held all challenged claims unpatentable
        0 = anything else (institution denied, discretionary denial, settled,
            terminated procedurally, FWD where any claim survived)
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
    model_version: str
    features_version: str
    predicted_at: datetime
    prob_cancelled: float
    predicted_cancelled: int
    feature_snapshot_id: str | None = None


class ModelMetrics(BaseModel):
    """Evaluation metrics for a single trained model on a held-out split."""

    model_config = ConfigDict(extra="ignore")

    model_name: str
    accuracy: float
    roc_auc: float
    classification_report: dict[str, Any]


class AdmissibilityPartition(BaseModel):
    """Outcome of partitioning a trial directory's documents by T0 admissibility."""

    t0: str
    n_admissible: int
    n_excluded: int
    pdfs_moved: int


# ---------------------------------------------------------------------------
# Ingestion pipeline seams (see docs/plans/2026-04-29-ingestion-pipeline.md §6)
# ---------------------------------------------------------------------------


class Petition(BaseModel):
    """One trial's picked petition row, produced by `parse.petitions`.

    Built from `documentData.*` paths only. The `trialMetaData` block on a
    document row is the live trial header lagged by the documents-endpoint
    indexer cadence and is NOT frozen at T₀ — see `docs/api/proceedings.md`.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
    petition_document_id: str
    petition_title: str | None = None
    petition_number: str | None = None
    petition_filing_date_doc: date
    petition_pdf_uri: str
    petition_category: str | None = None


class PatentFetchResult(BaseModel):
    """One application fetch outcome emitted by `ingest.fetch.fetch_patents`.

    `raw_record` is None for failed fetches (404, 5xx, transport error,
    missing record) — the fetcher logs the failure and moves on; the
    next cron run retries automatically.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    application_number: str
    raw_record: dict[str, Any] | None = None


class DecisionPdfFailure(BaseModel):
    """One failed FWD-PDF download. Persisted as a row in
    `Frame.DECISION_PDF_FAILURES`; the gap-detector skips doc_ids whose
    latest failure is within `--retry-after-days` so transient 5xxs don't
    permanently quarantine but a doc isn't retried every cron either.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
    document_identifier: str
    file_download_uri: str
    reason: DecisionPdfFailureReason
    http_status: int | None = None
    error: str | None = None
    failed_at: datetime


class DecisionPdfFetchResult(BaseModel):
    """One FWD download outcome emitted by `ingest.fetch.fetch_decision_pdfs`.

    On success, `bytes_written` is the size of the extracted text saved
    under `Stage.DECISION_TEXTS / <doc_id>.txt` (the binary PDF is never
    persisted); on failure, `failure` carries the diagnostic row to
    append to `Frame.DECISION_PDF_FAILURES`. Mutually exclusive —
    exactly one of the two is populated.
    """

    model_config = ConfigDict(extra="ignore")

    document_identifier: str
    bytes_written: int | None = None
    failure: DecisionPdfFailure | None = None


class PatentEvent(BaseModel):
    """One entry from `eventDataBag` of a patent file wrapper."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_date: date | None = Field(default=None, alias="eventDate")
    event_code: str | None = Field(default=None, alias="eventCode")


class Assignee(BaseModel):
    """One assignee inside an `assignmentBag` entry's `assigneeBag`."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    assignee_name: str | None = Field(default=None, alias="assigneeNameText")


class PatentAssignment(BaseModel):
    """One entry from `assignmentBag` of a patent file wrapper."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    assignment_received_date: date | None = Field(default=None, alias="assignmentReceivedDate")
    assignment_recorded_date: date | None = Field(default=None, alias="assignmentRecordedDate")
    assignees: list[Assignee] = Field(default_factory=list, alias="assigneeBag")


class ParentApplication(BaseModel):
    """One entry from `parentContinuityBag` — currently only a count is consumed."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    application_number: str | None = Field(default=None, alias="parentApplicationNumberText")


class PatentFileWrapper(BaseModel):
    """**Contract 1** — typed view of a raw `/applications/{appNum}` payload.

    T₀-naïve, no trial context. Construct via
    `parse.patents.parse_patent_wrapper`, which unwraps the
    `patentFileWrapperDataBag` envelope and normalizes the
    inconsistently-typed `cpcClassificationBag` entries to a flat
    `list[str]` before validation. Consumed by
    `features.patents.cleanse_at_t0` to build `PatentSnapshot`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    application_number: str | None = Field(default=None, alias="applicationNumberText")
    cpc_classifications: list[str] = Field(default_factory=list)
    events: list[PatentEvent] = Field(default_factory=list, alias="eventDataBag")
    assignments: list[PatentAssignment] = Field(default_factory=list, alias="assignmentBag")
    parent_continuity: list[ParentApplication] = Field(
        default_factory=list, alias="parentContinuityBag"
    )


class PatentSnapshot(BaseModel):
    """**Contract 2** — T₀-cleansed per-(trial, application) view of a patent.

    Produced by `features.patents.cleanse_at_t0(wrapper, trial_number,
    petition_filing_date, ...)`. Carries the same nested bag types as
    `PatentFileWrapper` but with leakage already removed:

      - `events`: drops rows with `event_date >= T₀` AND drops banned
        categories (`TRIAL*` event codes — the label leaking through).
      - `assignments`: drops rows whose received/recorded date is `>= T₀`
        (or has no usable date).
      - `cpc_classifications`, `parent_continuity`: undated, passed through.

    Downstream feature extractors (count features, future text or
    embedding features) consume the snapshot and never re-implement
    leakage discipline. The `petition_filing_date` field is kept on the
    snapshot so consumers like `extract_features` can compute
    `days_since_last_assignment` without re-passing T₀.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
    application_number: str
    petition_filing_date: date

    cpc_classifications: list[str] = Field(default_factory=list)
    events: list[PatentEvent] = Field(default_factory=list)
    assignments: list[PatentAssignment] = Field(default_factory=list)
    parent_continuity: list[ParentApplication] = Field(default_factory=list)


class PatentFeatures(BaseModel):
    """Numerical features per (trial, application), derived from a
    `PatentSnapshot`.

    Produced by `features.patents.extract_features(snapshot)`. Pure
    counting + span computation — no leakage logic, since the snapshot is
    already cleansed.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
    application_number: str
    cpc_section: str | None = None
    n_events_pre_t0: int = 0
    prosecution_span_days: int | None = None
    n_pe_pre_t0: int = 0
    n_ex_pre_t0: int = 0
    n_aa_pre_t0: int = 0
    n_ad_pre_t0: int = 0
    n_iss_pre_t0: int = 0
    n_maint_pre_t0: int = 0
    n_other_pre_t0: int = 0
    n_office_actions: int = 0
    n_ids_filings: int = 0
    n_assignments_pre_t0: int = 0
    n_distinct_assignees_pre_t0: int = 0
    days_since_last_assignment: int | None = None
    n_parent_applications: int = 0
    days_grant_to_petition: int | None = None


class JoinedTrial(BaseModel):
    """Final structured-feature frame: trials ⟕ petitions ⟕ patent_features.

    Quarantined trials (no pickable petition) are excluded entirely — the
    join is left on petitions, not trials. `patent_features` is None when
    the application number is missing or the patent fetch failed.
    """

    model_config = ConfigDict(extra="ignore")

    # Proceedings-derived (mirrors the flatten output of the proceedings parser)
    trial_number: str
    trial_type: str | None = None
    trial_status: str | None = None
    petition_filing_date: date
    accorded_filing_date: date | None = None
    institution_decision_date: date | None = None
    latest_decision_date: date | None = None
    termination_date: date | None = None
    patent_number: str | None = None
    owner_real_party: str | None = None
    owner_counsel: str | None = None
    grant_date: date | None = None
    group_art_unit: str | None = None
    technology_center: str | None = None
    inventor_name: str | None = None
    application_number: str | None = None
    petitioner_real_party: str | None = None

    cancelled: int

    # Petition seam
    petition_pdf_uri: str
    petition_filing_date_doc: date

    # Patent seam
    patent_features: PatentFeatures | None = None


class JoinReport(BaseModel):
    """Audit counts for a single `join_all` run."""

    model_config = ConfigDict(extra="ignore")

    n_trials_input: int
    n_trials_labeled: int
    n_petition_t0_mismatch: int
    n_joined: int
    n_with_patent_features: int


class PdfFetchManifestRow(BaseModel):
    """Audit row recorded for every petition PDF download attempt.

    Failures keep `bytes`/`sha256` None and set `error` + `http_status`;
    failed downloads quarantine, they don't crash the run.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
    bytes: int | None = None
    sha256: str | None = None
    http_status: int | None = None
    fetched_at: datetime
    error: str | None = None


class PetitionTextDoc(BaseModel):
    """Tier 0 — raw extracted petition text. Persisted as `*.json.gz`.

    Source-of-truth for re-extraction so we never re-download the PDF to
    recompute Tier 1/2 features.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
    page_count: int
    char_count: int
    pages: list[str]
    pdfplumber_version: str
    extracted_at: datetime


class PetitionTextFeatures(BaseModel):
    """Tier 1 (structural) + Tier 2 (statutory + procedural) features per petition.

    `petition_word_count` is None when the Certificate of Word Count is
    missing/unparseable (~1–3% empirically) — honest missingness over a
    biased `len(re.findall(...))` proxy. Every other counter defaults to 0
    / False on extractor miss, not None.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str

    # Tier 1 — structural / volumetric
    petition_word_count: int | None = None
    petition_page_count: int
    n_claims_challenged: int = 0
    n_grounds: int = 0
    n_exhibits: int = 0
    n_prior_art_refs: int = 0
    n_expert_declarations: int = 0

    # Tier 2 — statutory + procedural posture
    n_grounds_102: int = 0
    n_grounds_103: int = 0
    has_sotera_stipulation: bool = False
    mentions_fintiv_factors: bool = False
    discloses_prior_iprs_same_patent: bool = False
    n_real_parties_in_interest: int = 0
    claim_construction_disputed_terms: int = 0

    text_doc_sha256: str
    extracted_at: datetime


class FwdOutcomePattern(NamedTuple):
    """One FWD outcome regex bound to its label, applied to title or opinion text."""

    name: str
    pattern: re.Pattern[str]
    label: int
