"""Identify the petition document for each trial and assemble per-trial rows."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from ml_uspto.parse.schemas.constants import (
    EXHIBIT_CATEGORIES,
    PAPER_NUMBER_CEILING,
)
from ml_uspto.parse.schemas.patterns import BLACKLIST, PETITION_TITLE
from ml_uspto.schemas.models import Petition

logger = logging.getLogger(__name__)


def pick_petition(rows: Iterable[Mapping[str, Any]]) -> dict | None:
    """Return the petition row from a trial's full document list, or None.

    `documentCategory` is unreliable across the taxonomy drift (post-2022
    trials use `PETITION`; pre-2022 trials lump petitions into the legacy
    `Paper` catch-all alongside motions, orders, and mandatory notices —
    see `docs/api/proceedings.md`), so the picker is title-driven.

    Layers, applied in order:
      1. Drop exhibits (`documentCategory` ∈ `EXHIBIT_CATEGORIES`).
      2. Drop high paper numbers (`>= PAPER_NUMBER_CEILING`) — 94% of
         real petitions sit at paper 1–3 and none observed past paper 8
         across 239 stratified trials.
      3. Title must match `PETITION_TITLE`.
      4. Title must not match `BLACKLIST` — strips near-misses like
         "Power of Attorney", "Notice of Filing Date Accorded to
         Petition", "Petitioner's Reply", "Sur-Reply", joinder motions.
      5. Lowest `documentNumber` wins — picks the *original* petition
         over any "Corrected Petition" filed later.

    Empirical validation (239 trials stratified 2012–2025 + all terminal
    statuses): 98.7% clean recall, 0 false positives.

    Args:
        rows: All document rows for a single trial.

    Returns:
        The picked row dict, or `None` if no candidate survives the
        filter cascade.
    """
    candidates: list[tuple[int, dict]] = []
    for row in rows:
        dd = row.get("documentData") or {}
        title = dd.get("documentTitleText") or dd.get("documentName") or ""
        category = dd.get("documentCategory") or ""
        number = dd.get("documentNumber") or 9999

        if category in EXHIBIT_CATEGORIES:
            continue
        if number >= PAPER_NUMBER_CEILING:
            continue
        if not PETITION_TITLE.search(title):
            continue
        if BLACKLIST.search(title):
            continue

        candidates.append((number, dict(row)))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


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


def assemble_petitions(
    raw_doc_records: Iterable[Mapping[str, Any]],
) -> list[Petition]:
    """Group raw document records by trial, run picker, return petitions.

    Each `Petition` row is built from `picked["documentData"]` only,
    never from `picked["trialMetaData"]` — that block is a denormalized
    trial header that lags the proceedings side, and reading it risks
    silent post-T₀ contamination (`docs/api/proceedings.md`).

    Args:
        raw_doc_records: Raw document records yielded by the
            corpus-wide petition scan.

    Returns:
        One `Petition` per trial that passed the picker. Trials with
        no pickable petition are silently omitted — the joiner detects
        them as `labeled.trial_number ∖ petitions.trial_number`.
    """
    groups = _group_by_trial(raw_doc_records)

    petitions: list[Petition] = []
    n_picker_miss = 0
    for trial_number, rows in groups.items():
        picked = pick_petition(rows)
        if picked is None:
            n_picker_miss += 1
            continue
        petitions.append(_build_petition(trial_number, picked))

    logger.info(
        "Assembled %d petitions (%d trials with rows but no picker hit)",
        len(petitions),
        n_picker_miss,
    )
    return petitions


__all__ = ["pick_petition", "assemble_petitions"]
