"""All Pydantic models for the project, in one place.

Field names mirror the camelCase API payload via Pydantic aliases so models
populate either from raw JSON (`Proceeding.model_validate(payload)`) or from
snake_case kwargs (`Proceeding(trial_number=...)`).
"""

import re
from datetime import date, datetime
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

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


# ---------------------------------------------------------------------------
# Ingestion pipeline seams (see docs/plans/2026-04-29-ingestion-pipeline.md §6)
# ---------------------------------------------------------------------------


class Petition(BaseModel):
    """One trial's picked petition row, produced by `parse.petitions`.

    Scaffolding only — *not* the source of model features. The role of
    `Petition` is to identify which filing is the petition, capture the
    documents-side filing date for the T₀ cross-check, and carry
    `petition_pdf_uri` as a handle. The actual petition features live
    on `PetitionTextFeatures`, populated by the deferred Tier 1/2
    pipeline (see `docs/scope/prediction_scope.md` §5 and
    `docs/features/admissible_documents_analysis.md`).

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


class DecisionPdfFetchResult(BaseModel):
    """One FWD download outcome emitted by `ingest.fetch.fetch_decision_pdfs`.

    `bytes_written` is the size of the extracted text saved under
    `Stage.DECISION_TEXTS / <doc_id>.txt` on success, and `None` on
    failure (the binary PDF is never persisted). Failures are logged
    inline; a permanently-broken doc just reappears on the next cron's
    gap-detector pass — cheap at ~30–50 weekly deltas.
    """

    model_config = ConfigDict(extra="ignore")

    document_identifier: str
    bytes_written: int | None = None


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
    """Typed view of a raw `/applications/{appNum}` payload.

    Constructed via `parse.patents.parse_patent_wrapper`, which unwraps
    the `patentFileWrapperDataBag` envelope and normalizes the
    inconsistently-typed `cpcClassificationBag` entries to a flat
    `list[str]` before validation. Consumed by
    `parse.patents.to_flat_record` to assemble the per-app row that
    lands in `patents.parquet` (scalars + parallel-array cols). T₀
    leakage discipline lives downstream in
    `features.transforms.build_features`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    application_number: str | None = Field(default=None, alias="applicationNumberText")
    cpc_classifications: list[str] = Field(default_factory=list)
    events: list[PatentEvent] = Field(default_factory=list, alias="eventDataBag")
    assignments: list[PatentAssignment] = Field(default_factory=list, alias="assignmentBag")
    parent_continuity: list[ParentApplication] = Field(
        default_factory=list, alias="parentContinuityBag"
    )


class JoinReport(BaseModel):
    """Audit counts for a single `join_all` run."""

    model_config = ConfigDict(extra="ignore")

    n_trials_input: int
    n_trials_labeled: int
    n_petition_t0_mismatch: int
    n_joined: int


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

    Produced by the deferred v2 pipeline that fetches each petition PDF
    via the `petition_pdf_uri` handle on `Petition`, runs pdfplumber +
    structured-regex extraction over the text, and emits this row. v1
    ships the schema but not the producer (`docs/scope/prediction_scope.md`
    §5; full feature catalog in `docs/features/admissible_documents_analysis.md`).

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
