"""Document-side feature engineering for trial documents.

Mirrors `features.patents` for the document corpus: all document-derived
feature logic lives here, starting with the T₀ admissibility gate and
expanding to per-document text / OCR / embedding extractors as those
land. Anything that turns trial documents into model-ready signals is
this module's domain.

Currently exposed:

  - `is_admissible(record, t0)` / `partition_records(records, t0)` — pure
    T₀ leakage filter. A document is admissible iff its
    `documentFilingDate` is on or before T₀ (`petition_filing_date`).
    Per `docs/scope/prediction_scope.md` §4: papers bundled with the
    petition (Power of Attorney, mandatory notices, exhibits 1xxx) carry
    `documentFilingDate == T0` and pass; everything else (POPR, POR,
    Reply, Sur-Reply, orders, decisions, CAFC mandate, late exhibits) is
    filed strictly after T₀ and is excluded. Type-based filters are not
    needed — the date check is sufficient and correct by construction.

  - `partition_trial_dir(...)` — operational helper that applies the
    same gate to an on-disk trial directory and moves excluded PDFs into
    `_excluded_post_T0/` so a downstream text extractor only sees the
    admissible slice.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ml_uspto.schemas.models import AdmissibilityPartition


def is_admissible(record: Mapping[str, Any], t0: str) -> bool:
    fd = record.get("documentData", {}).get("documentFilingDate", "")
    return bool(fd) and fd <= t0


def partition_records(
    records: Iterable[Mapping[str, Any]], t0: str
) -> tuple[list[dict], list[dict]]:
    admissible: list[dict] = []
    excluded: list[dict] = []
    for r in records:
        (admissible if is_admissible(r, t0) else excluded).append(dict(r))
    return admissible, excluded


def partition_trial_dir(trial_dir: Path, t0: str | None = None) -> AdmissibilityPartition:
    """Partition a trial directory's PDFs by admissibility.

    Reads `all_documents.json`, partitions records by T0, writes
    `admissible_documents.json` and `excluded_documents.json` manifests,
    and moves excluded PDFs into `_excluded_post_T0/`.

    If `t0` is None, reads it from `metadata.json`
    (`trialMetaData.petitionFilingDate`).
    """
    trial_dir = Path(trial_dir)

    if t0 is None:
        meta = json.loads((trial_dir / "metadata.json").read_text())
        if "patentTrialProceedingDataBag" in meta:
            meta = meta["patentTrialProceedingDataBag"][0]
        t0 = meta["trialMetaData"]["petitionFilingDate"]

    all_docs = json.loads((trial_dir / "all_documents.json").read_text())
    records = all_docs["records"] if isinstance(all_docs, dict) else all_docs

    admissible, excluded = partition_records(records, t0)

    (trial_dir / "admissible_documents.json").write_text(
        json.dumps({"t0": t0, "count": len(admissible), "records": admissible}, indent=2)
    )
    (trial_dir / "excluded_documents.json").write_text(
        json.dumps({"t0": t0, "count": len(excluded), "records": excluded}, indent=2)
    )

    pdf_root = trial_dir / trial_dir.name
    excluded_dir = pdf_root / "_excluded_post_T0"
    moved = 0
    if pdf_root.is_dir():
        excluded_dir.mkdir(exist_ok=True)
        excluded_ids = {r["documentData"]["documentIdentifier"] for r in excluded}
        for pdf in pdf_root.glob("*.pdf"):
            if pdf.stem in excluded_ids:
                shutil.move(str(pdf), str(excluded_dir / pdf.name))
                moved += 1

    return AdmissibilityPartition(
        t0=t0,
        n_admissible=len(admissible),
        n_excluded=len(excluded),
        pdfs_moved=moved,
    )


if __name__ == "__main__":
    import sys

    result = partition_trial_dir(Path(sys.argv[1]))
    print(result.model_dump_json(indent=2))
