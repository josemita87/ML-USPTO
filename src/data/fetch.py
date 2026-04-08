"""Fetch IPR proceedings data from USPTO and save raw dataset."""

import logging

import pandas as pd

from src.data.client import USPTOClient

logger = logging.getLogger(__name__)


def fetch_ipr_proceedings(
    client: USPTOClient, max_pages: int = 50, page_size: int = 100
) -> pd.DataFrame:
    """Fetch all proceedings, paginating through results.

    The API query param is full-text only — we filter to IPR client-side.
    """
    all_records = []
    offset = 0

    for page in range(max_pages):
        logger.info("Fetching page %d (offset=%d)", page + 1, offset)
        data = client.search_proceedings(
            query="IPR", offset=offset, limit=page_size
        )

        records = data.get("patentTrialProceedingDataBag", [])
        if not records:
            logger.info("No more records at offset %d", offset)
            break

        all_records.extend(records)
        offset += page_size

        total = data.get("count", 0)
        if offset >= total:
            break

    logger.info("Fetched %d total proceedings", len(all_records))
    return _flatten_proceedings(all_records)


def fetch_ipr_decisions(
    client: USPTOClient, max_pages: int = 50, page_size: int = 100
) -> pd.DataFrame:
    """Fetch all IPR decisions, paginating through results."""
    all_records = []
    offset = 0

    for page in range(max_pages):
        logger.info("Fetching decisions page %d (offset=%d)", page + 1, offset)
        data = client.search_decisions(
            query="IPR", offset=offset, limit=page_size
        )

        records = data.get("patentTrialDocumentDataBag", [])
        if not records:
            logger.info("No more decision records at offset %d", offset)
            break

        all_records.extend(records)
        offset += page_size

        total = data.get("count", 0)
        if offset >= total:
            break

    logger.info("Fetched %d total decisions", len(all_records))
    return _flatten_decisions(all_records)


def _flatten_decisions(records: list[dict]) -> pd.DataFrame:
    """Flatten nested decision JSON into a flat DataFrame."""
    rows = []
    for rec in records:
        decision = rec.get("decisionData", {})
        doc = rec.get("documentData", {})

        row = {
            "trial_number": rec.get("trialNumber"),
            "decision_issue_date": decision.get("decisionIssueDate"),
            "decision_type": decision.get("decisionTypeCategory"),
            "trial_outcome": decision.get("trialOutcomeCategory"),
            "statutes_and_rules": decision.get("statuteAndRuleBag"),
            "document_name": doc.get("documentName"),
            "document_title": doc.get("documentTitleText"),
            "document_type": doc.get("documentTypeDescriptionText"),
            "document_filing_date": doc.get("documentFilingDate"),
            "filing_party": doc.get("filingPartyCategory"),
        }
        rows.append(row)

    return pd.DataFrame(rows)


def _flatten_proceedings(records: list[dict]) -> pd.DataFrame:
    """Flatten nested proceeding JSON into a flat DataFrame."""
    rows = []
    for rec in records:
        meta = rec.get("trialMetaData", {})
        owner = rec.get("patentOwnerData", {})
        petitioner = rec.get("regularPetitionerData", {})

        row = {
            "trial_number": rec.get("trialNumber"),
            # Trial metadata
            "trial_type": meta.get("trialTypeCode"),
            "trial_status": meta.get("trialStatusCategory"),
            "petition_filing_date": meta.get("petitionFilingDate"),
            "accorded_filing_date": meta.get("accordedFilingDate"),
            "institution_decision_date": meta.get("institutionDecisionDate"),
            "latest_decision_date": meta.get("latestDecisionDate"),
            "termination_date": meta.get("terminationDate"),
            # Patent owner data
            "patent_number": owner.get("patentNumber"),
            "owner_real_party": owner.get("realPartyInInterestName"),
            "owner_counsel": owner.get("counselName"),
            "grant_date": owner.get("grantDate"),
            "group_art_unit": owner.get("groupArtUnitNumber"),
            "technology_center": owner.get("technologyCenterNumber"),
            "inventor_name": owner.get("inventorName"),
            "application_number": owner.get("applicationNumberText"),
            # Petitioner data
            "petitioner_real_party": petitioner.get("realPartyInInterestName"),
            "petitioner_counsel": petitioner.get("counselName"),
        }
        rows.append(row)

    return pd.DataFrame(rows)
