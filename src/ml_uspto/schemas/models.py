"""All Pydantic models for the project, in one place."""

import re
from datetime import date, datetime
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# /trials/proceedings
# ---------------------------------------------------------------------------


class TrialMetaData(BaseModel):
    """`trialMetaData` block from a `/trials/proceedings` row."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    trial_type: str | None = Field(default=None, alias="trialTypeCode")
    trial_status: str | None = Field(default=None, alias="trialStatusCategory")
    petition_filing_date: date | None = Field(default=None, alias="petitionFilingDate")
    accorded_filing_date: date | None = Field(default=None, alias="accordedFilingDate")
    institution_decision_date: date | None = Field(default=None, alias="institutionDecisionDate")
    latest_decision_date: date | None = Field(default=None, alias="latestDecisionDate")
    termination_date: date | None = Field(default=None, alias="terminationDate")


class PatentOwnerData(BaseModel):
    """`patentOwnerData` block — challenged-patent metadata frozen at petition filing."""

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
    """`regularPetitionerData` block — challenger party + counsel."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    real_party: str | None = Field(default=None, alias="realPartyInInterestName")
    counsel: str | None = Field(default=None, alias="counselName")


class Proceeding(BaseModel):
    """One row from `/trials/proceedings` — the per-trial header record."""

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
    """`documentData` block — per-filing metadata (id, title, type, party, date)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    document_identifier: str | None = Field(default=None, alias="documentIdentifier")
    document_name: str | None = Field(default=None, alias="documentName")
    document_title: str | None = Field(default=None, alias="documentTitleText")
    document_type: str | None = Field(default=None, alias="documentTypeDescriptionText")
    document_filing_date: date | None = Field(default=None, alias="documentFilingDate")
    filing_party: str | None = Field(default=None, alias="filingPartyCategory")


class DecisionData(BaseModel):
    """`decisionData` block — present on FWD (Final Written Decision) records and the like."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    decision_issue_date: date | None = Field(default=None, alias="decisionIssueDate")
    decision_type: str | None = Field(default=None, alias="decisionTypeCategory")
    trial_outcome: str | None = Field(default=None, alias="trialOutcomeCategory")
    statutes_and_rules: list[str] | None = Field(default=None, alias="statuteAndRuleBag")
    issue_types: list[str] | None = Field(default=None, alias="issueTypeBag")


class TrialDocument(BaseModel):
    """One row from `/trials/{id}/documents` or `/trials/decisions`."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    trial_number: str = Field(alias="trialNumber")
    document_data: DocumentData = Field(default_factory=DocumentData, alias="documentData")
    decision_data: DecisionData | None = Field(default=None, alias="decisionData")


# ---------------------------------------------------------------------------
# Feature-engineering boundary
# ---------------------------------------------------------------------------


class FeatureRow(BaseModel):
    """Mirrors the leakage-free intermediate frame produced by
    `ml_uspto.features.transforms.build_features`.

    Cross-row encodings (one-hot, frequency, imputation) are *not*
    fields here — they are applied downstream by
    `ml_uspto.models.preprocessing.build_preprocessor` so they can be
    fit on training rows only.

    Both training and inference paths construct `FeatureRow` instances;
    this is the single shared codepath that prevents training/serving
    skew on the per-row transforms.
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str

    filing_year: int | None = None
    filing_month: int | None = None
    art_unit_group: int | None = None

    days_grant_to_petition: int | None = None
    days_grant_to_petition_missing: int | None = None
    prosecution_span_days: int | None = None
    prosecution_span_days_missing: int | None = None
    days_since_last_assignment: int | None = None
    no_recorded_assignment: int | None = None

    # Raw categoricals — encoded downstream by the modeling preprocessor.
    technology_center: str | None = None
    cpc_section: str | None = None
    petitioner_real_party: str | None = None
    owner_real_party: str | None = None


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


class CalibrationBin(BaseModel):
    """One row of the held-out reliability curve."""

    model_config = ConfigDict(extra="ignore")

    bin_lower: float
    bin_upper: float
    mean_predicted: float
    fraction_positive: float
    n_samples: int


class ModelMetrics(BaseModel):
    """Evaluation metrics for a single trained model on a held-out split."""

    model_config = ConfigDict(extra="ignore")

    model_name: str
    accuracy: float
    roc_auc: float
    classification_report: dict[str, Any]
    # Calibration probes the gap between predicted and observed
    # cancellation rates — RandomForest's `predict_proba` is known to be
    # miscalibrated, so we measure but do not correct (decision-support
    # use case only).
    calibration_bins: list[CalibrationBin] | None = None


# ---------------------------------------------------------------------------
# Ingestion pipeline seams
# ---------------------------------------------------------------------------


class Petition(BaseModel):
    """One trial's picked petition row, produced by `parse.petitions`.

    Scaffolding only — *not* the source of model features. The role of
    `Petition` is to identify which filing is the petition, capture the
    documents-side filing date for the T₀ cross-check, and carry
    `petition_pdf_uri` as a handle for the petition-text ingest driver.
    The actual petition features land as columns on the joined frame
    via `Frame.PETITION_TEXTS` and are computed in
    `features.transforms.build_features` (see
    `docs/scope/prediction_scope.md` §5 and
    `docs/engineering/features/admissible_documents_analysis.md`).

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


class PetitionPdfFetchResult(BaseModel):
    """One petition-PDF download outcome from `ingest.fetch.fetch_petition_pdfs`.

    Mirrors `DecisionPdfFetchResult` but keyed by `trial_number`
    (petitions are 1-per-trial after `parse.petition_picker`).
    `bytes_written` is `None` on failure; binaries are never persisted
    (text-only cache mirrors the FWD policy).
    """

    model_config = ConfigDict(extra="ignore")

    trial_number: str
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




class GridSearchEntry(BaseModel):
    """One (params → CV scores) row from a `grid_search_cv` sweep."""

    model_config = ConfigDict(extra="ignore")

    model_name: str
    params: dict[str, Any]
    mean_roc_auc: float
    std_roc_auc: float
    fold_roc_auc: list[float]
    mean_fit_time: float


class GridSearchResult(BaseModel):
    """All entries from a `grid_search_cv` sweep plus the best combo by mean ROC-AUC."""

    model_config = ConfigDict(extra="ignore")

    model_name: str
    n_splits: int
    entries: list[GridSearchEntry]
    best_params: dict[str, Any]
    best_mean_roc_auc: float


class FwdOutcomePattern(NamedTuple):
    """One FWD outcome regex bound to its label, applied to title or opinion text."""

    name: str
    pattern: re.Pattern[str]
    label: int
