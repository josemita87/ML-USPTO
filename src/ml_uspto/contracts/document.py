"""Pydantic models for the `/trials/decisions` and `/trials/{id}/documents` surfaces.

A `TrialDocument` represents one row of the decisions endpoint: a denormalized
join of (trial_number, document metadata, optional decisionData).
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


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
