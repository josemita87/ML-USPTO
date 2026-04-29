"""Assemble per-trial petition rows from corpus-wide documents/search output.

Stage 2 fetches ~301K raw document rows in one paginated scan filtered to
`documentCategory IN ["PETITION", "Paper"]`. This module groups those raw
rows by `trialNumber` and runs `pick_petition` per group to pick the one
original-petition row per trial. A single picker pass handles both the
post-2022 PETITION bucket (which can still contain Corrected/Joinder
duplicates) and the pre-2022 Paper catch-all — see
`docs/api/proceedings.md` "Petition coverage and the category-taxonomy
drift".

Per-group outcomes:

  - picker hits → `Petition` row built from `picked["documentData"]`
    only, NEVER `picked["trialMetaData"]`. The trialMetaData on a
    documents row is the trial header denormalized at indexer time and
    lags the proceedings side; pulling features from it risks silent
    post-T₀ contamination (see `docs/api/proceedings.md` "What we
    observed").
  - picker miss → `QuarantineEntry(reason="picker_no_match")` with up to
    five sample titles to aid manual triage.
  - trial in `trials` with zero document rows in the corpus →
    `QuarantineEntry(reason="no_documents")`.

Quarantine is explicit; trials are never silently dropped from the
pipeline. The proceedings-side `petition_filing_date` is the canonical
T₀ — `documentFilingDate` is cross-checked and any mismatch is logged
but not raised.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

import pandas as pd

from ml_uspto.parse.petition_picker import pick_petition
from ml_uspto.parse.schemas.constants import QUARANTINE_SAMPLE_TITLES_LIMIT
from ml_uspto.schemas.enums import QuarantineReason
from ml_uspto.schemas.models import Petition, QuarantineEntry

logger = logging.getLogger(__name__)


def _group_by_trial(records: Iterable[Mapping[str, Any]]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        trial = rec.get("trialNumber")
        if trial:
            groups[trial].append(dict(rec))
    return groups


def _build_petition(trial_number: str, picked: Mapping[str, Any]) -> Petition:
    dd = picked["documentData"]
    number = dd.get("documentNumber")
    return Petition(
        trial_number=trial_number,
        petition_document_id=dd["documentIdentifier"],
        petition_title=dd.get("documentTitleText") or dd.get("documentName"),
        petition_number=str(number) if number is not None else None,
        petition_filing_date_doc=dd["documentFilingDate"],
        petition_pdf_uri=dd["fileDownloadURI"],
        petition_category=dd.get("documentCategory"),
    )


def _to_date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.Timestamp(value).date()
    except (ValueError, TypeError):
        return None


def assemble_petitions(
    raw_doc_records: Iterable[Mapping[str, Any]],
    trials: pd.DataFrame,
) -> tuple[list[Petition], list[QuarantineEntry]]:
    """Group raw document records by trial, run picker, return petitions + quarantine.

    `trials` must carry at least `trial_number`; `petition_filing_date` is
    consulted for the T₀ cross-check when present.
    """
    groups = _group_by_trial(raw_doc_records)

    proceedings_t0: dict[str, date | None] = {}
    if "petition_filing_date" in trials.columns:
        for tn, t0 in trials.set_index("trial_number")["petition_filing_date"].items():
            proceedings_t0[str(tn)] = _to_date(t0)

    petitions: list[Petition] = []
    quarantine: list[QuarantineEntry] = []

    for trial_number, rows in groups.items():
        picked = pick_petition(rows)
        if picked is None:
            sample_titles = [
                (row.get("documentData") or {}).get("documentTitleText")
                or (row.get("documentData") or {}).get("documentName")
                or ""
                for row in rows[:QUARANTINE_SAMPLE_TITLES_LIMIT]
            ]
            quarantine.append(
                QuarantineEntry(
                    trial_number=trial_number,
                    reason=QuarantineReason.PICKER_NO_MATCH,
                    n_candidates=len(rows),
                    sample_titles=sample_titles,
                )
            )
            continue

        petition = _build_petition(trial_number, picked)
        petitions.append(petition)

        t0 = proceedings_t0.get(trial_number)
        if t0 is not None and petition.petition_filing_date_doc != t0:
            logger.warning(
                "T₀ mismatch for %s: documents=%s proceedings=%s — proceedings wins",
                trial_number,
                petition.petition_filing_date_doc,
                t0,
            )

    seen = set(groups.keys())
    for tn in trials["trial_number"].astype(str):
        if tn not in seen:
            quarantine.append(
                QuarantineEntry(
                    trial_number=tn,
                    reason=QuarantineReason.NO_DOCUMENTS,
                    n_candidates=0,
                )
            )

    by_reason = {r: sum(1 for q in quarantine if q.reason is r) for r in QuarantineReason}
    logger.info(
        "Assembled %d petitions, %d quarantined (%s)",
        len(petitions),
        len(quarantine),
        ", ".join(f"{n} {r.value}" for r, n in by_reason.items()),
    )
    return petitions, quarantine


__all__ = ["assemble_petitions"]
