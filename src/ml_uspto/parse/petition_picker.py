"""Identify the petition document among a trial's filings.

The PTAB documents endpoint returns one row per filing (`POST
/trials/documents/search` filtered by `trialNumber`). The petition is the
single row we actually care about for feature extraction, but its identity
must be inferred — `documentCategory` is unreliable across the taxonomy
drift (post-2022 trials use `PETITION`; pre-2022 trials lump petitions into
the legacy `Paper` catch-all bucket alongside motions, orders, and
mandatory notices). See `docs/api/proceedings.md` "Petition coverage and
the category-taxonomy drift" for the mechanic.

`pick_petition` resolves this with a layered filter applied to the row
list:

1. Drop exhibits (`documentCategory` ∈ EXHIBIT_CATEGORIES).
2. Drop high paper numbers (`documentNumber >= PAPER_NUMBER_CEILING`) —
   empirically 94% of real petitions sit at paper 1–3 and none observed
   past paper 8 across 239 stratified trials.
3. Title must match PETITION_TITLE — covers "Petition for…",
   "Inter Partes Review of [patent]", "Request for IPR…", and the
   "Petitioner's Petition for…" phrasing.
4. Title must NOT match BLACKLIST — strips out near-misses like
   "Power of Attorney", "Notice of Filing Date Accorded to Petition",
   "Petitioner's Reply", "Sur-Reply", joinder motions, etc.
5. Among the survivors, take the lowest `documentNumber` — this picks the
   *original* petition over any "Corrected Petition" filed later.

Empirical validation (probe of 239 trials stratified across 2012–2025 +
all terminal statuses): 98.7% clean recall, 0 false positives. The 1.3%
miss rate falls through to a quarantine list rather than feeding wrong
PDFs into the feature pipeline — this is the right failure mode for a
leakage-sensitive system.

Picker constants live in `config/petition_picker.yaml` and are exposed via
`ml_uspto.parse.schemas.constants`.

Usage:
    from ml_uspto.parse.petition_picker import pick_petition
    payload = client.search_documents_post(filters=[{"name": "trialNumber", "value": [t]}])
    rows = payload["patentTrialDocumentDataBag"]
    petition_row = pick_petition(rows)  # None if nothing matched
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ml_uspto.parse.schemas.constants import (
    EXHIBIT_CATEGORIES,
    PAPER_NUMBER_CEILING,
)
from ml_uspto.parse.schemas.patterns import BLACKLIST, PETITION_TITLE


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
