"""All Pydantic models for the project, in one place.

Field names mirror the camelCase API payload via Pydantic aliases so models
populate either from raw JSON (`Proceeding.model_validate(payload)`) or from
snake_case kwargs (`Proceeding(trial_number=...)`).
"""

from datetime import date, datetime
from typing import Any

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
    trial_meta_data: TrialMetaData = Field(
        default_factory=TrialMetaData, alias="trialMetaData"
    )
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
