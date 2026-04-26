"""Pydantic models for the `/trials/proceedings` API surface.

Field names mirror the camelCase API payload via Pydantic aliases.
Models are populated either from raw JSON (`Proceeding.model_validate(payload)`)
or from snake_case kwargs (`Proceeding(trial_number=...)`).
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


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
