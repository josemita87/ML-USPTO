"""Identify the petition document for each trial and assemble per-trial rows.

Two public functions, one domain — both deal with picking the petition
filing out of a trial's documents/search rows:

  - `pick_petition(rows)` — runs the layered filter (exhibit drop, paper-
    number ceiling, title regex, blacklist, lowest-paper tiebreaker) on
    one trial's rows and returns the picked row or `None`. Pure function.

  - `assemble_petitions(records, trials)` — groups corpus-wide
    documents/search records by `trialNumber`, runs the picker per
    group, and returns `(petitions, quarantine)`. Quarantine is explicit:
    `picker_no_match` for groups where every candidate failed the filter,
    `no_documents` for trials present in `trials` but absent from the
    documents corpus.

The PTAB documents endpoint returns one row per filing (`POST
/trials/documents/search` filtered by `trialNumber`). The petition is the
single row we actually care about for feature extraction, but its
identity must be inferred — `documentCategory` is unreliable across the
taxonomy drift (post-2022 trials use `PETITION`; pre-2022 trials lump
petitions into the legacy `Paper` catch-all alongside motions, orders,
and mandatory notices). See `docs/api/proceedings.md` "Petition coverage
and the category-taxonomy drift" for the mechanic.

Picker layers (applied in order to each row):

1. Drop exhibits (`documentCategory` ∈ EXHIBIT_CATEGORIES).
2. Drop high paper numbers (`documentNumber >= PAPER_NUMBER_CEILING`) —
   empirically 94% of real petitions sit at paper 1–3 and none observed
   past paper 8 across 239 stratified trials.
3. Title must match PETITION_TITLE — covers "Petition for…", "Inter
   Partes Review of [patent]", "Request for IPR…", and the
   "Petitioner's Petition for…" phrasing.
4. Title must NOT match BLACKLIST — strips out near-misses like "Power of
   Attorney", "Notice of Filing Date Accorded to Petition",
   "Petitioner's Reply", "Sur-Reply", joinder motions, etc.
5. Among survivors, take the lowest `documentNumber` — picks the
   *original* petition over any "Corrected Petition" filed later.

Empirical validation (probe of 239 trials stratified across 2012–2025 +
all terminal statuses): 98.7% clean recall, 0 false positives. The 1.3%
miss rate falls through to a quarantine list rather than feeding wrong
PDFs into the feature pipeline — this is the right failure mode for a
leakage-sensitive system.

Picker constants live in `config/petition_picker.yaml` and are exposed
via `ml_uspto.parse.schemas.constants`.

Per-group outcomes from `assemble_petitions`:

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
from datetime import date
from typing import Any

import pandas as pd

from ml_uspto.parse.utils import to_date
from ml_uspto.parse.schemas.constants import (
    EXHIBIT_CATEGORIES,
    PAPER_NUMBER_CEILING,
    QUARANTINE_SAMPLE_TITLES_LIMIT,
)
from ml_uspto.parse.schemas.patterns import BLACKLIST, PETITION_TITLE
from ml_uspto.schemas.enums import QuarantineReason
from ml_uspto.schemas.models import Petition, QuarantineEntry

logger = logging.getLogger(__name__)


def pick_petition(rows: Iterable[Mapping[str, Any]]) -> dict | None:
    """Return the petition row from a trial's full document list, or None."""
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
            proceedings_t0[str(tn)] = to_date(t0)

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


__all__ = ["pick_petition", "assemble_petitions"]
