"""Stage A — download a stratified sample of FWDs to eyeball.

Walks `data/raw/decisions/page_*.json`, picks ~25 Final-Written-Decision
documents spanning year × `documentTypeDescriptionText` × title-pattern
diversity, downloads each PDF via `USPTOClient.download_pdf`, extracts
full text via pdfplumber, and caches the text under
`Stage.DECISION_TEXTS` keyed by `documentIdentifier`. Reports
extractability on every fetched PDF.

The sample manifest (`data/raw/decision_texts/_sample_manifest.json`)
captures the bucketing decisions so the picks are reproducible without
re-running the walk.
"""

import argparse
import io
import json
import logging
import random
import re
import time
from collections import defaultdict
from typing import Any

import pdfplumber

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.schemas.enums import Stage

logger = logging.getLogger(__name__)

# Title rows that already encode the outcome — useful as ground-truth in
# the eyeball pass; the regex must agree with these labels.
PARSEABLE_TITLE_RE = re.compile(
    r"determining\s+(all|no|some)\s+challenged\s+claims",
    re.IGNORECASE,
)


def _iter_fwd_candidates(storage: LocalStorage):
    """Yield (year, document_type, parseable_title, candidate_dict) for FWDs."""
    for _page_key, payload in storage.iter_objects(Stage.DECISIONS.value):
        for record in payload.get("patentTrialDocumentDataBag") or []:
            doc = record.get("documentData") or {}
            doc_type = (doc.get("documentTypeDescriptionText") or "").strip()
            if "Final Written Decision" not in doc_type:
                continue
            uri = doc.get("fileDownloadURI")
            ident = doc.get("documentIdentifier")
            if not uri or not ident:
                continue
            issue = (record.get("decisionData") or {}).get("decisionIssueDate") or ""
            year = issue[:4] if issue else "unknown"
            title = (doc.get("documentTitleText") or "").strip()
            yield {
                "year": year,
                "document_type": doc_type,
                "parseable_title": bool(PARSEABLE_TITLE_RE.search(title)),
                "trial_number": record.get("trialNumber"),
                "document_identifier": ident,
                "document_title": title,
                "file_download_uri": uri,
                "decision_issue_date": issue,
            }


def _stratified_sample(
    candidates: list[dict[str, Any]], target: int, seed: int
) -> list[dict[str, Any]]:
    """Round-robin pick across (year, document_type-bucket, parseable-title)."""
    rng = random.Random(seed)
    buckets: dict[tuple[str, str, bool], list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        # Collapse rarer document_type variants into a single "non-original"
        # bucket so they don't dominate the stratification.
        dt = "original" if "original" in c["document_type"].lower() else "remand_or_rehearing"
        buckets[(c["year"], dt, c["parseable_title"])].append(c)

    for items in buckets.values():
        rng.shuffle(items)

    keys = sorted(buckets.keys())
    rng.shuffle(keys)

    chosen: list[dict[str, Any]] = []
    while len(chosen) < target and any(buckets[k] for k in keys):
        for k in keys:
            if buckets[k]:
                chosen.append(buckets[k].pop())
                if len(chosen) >= target:
                    break
    return chosen


def _try_extract(pdf_bytes: bytes) -> tuple[str | None, dict[str, Any]]:
    """Return (text, report) for one PDF. Text is None on failure."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        return text, {
            "ok": True,
            "n_chars": len(text),
            "first_100": text[:100].replace("\n", " "),
        }
    except Exception as exc:
        return None, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    """Sample, download, and extract text from a stratified set of FWD PDFs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=25, help="number of PDFs to sample")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--rate-sleep",
        type=float,
        default=2.0,
        help="seconds to sleep between PDF downloads (USPTO ODP burst=1)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    storage = LocalStorage()
    candidates = list(_iter_fwd_candidates(storage))
    logger.info("Found %d FWD candidates in decisions cache", len(candidates))

    sample = _stratified_sample(candidates, target=args.target, seed=args.seed)
    logger.info("Picked %d for download", len(sample))

    manifest_dir = storage.raw_root / Stage.DECISION_TEXTS.value
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "_sample_manifest.json"

    client = USPTOClient()
    enriched: list[dict[str, Any]] = []
    for i, c in enumerate(sample, 1):
        ident = c["document_identifier"]
        cached_text = storage.load_blob(Stage.DECISION_TEXTS.value, ident, "txt")
        if cached_text is not None:
            logger.info("[%d/%d] cache hit %s", i, len(sample), ident)
            text = cached_text.decode("utf-8", errors="replace")
            report = {"ok": True, "n_chars": len(text), "first_100": text[:100].replace("\n", " ")}
            enriched.append({**c, "size_bytes": len(cached_text), "extract": report})
            continue

        logger.info(
            "[%d/%d] downloading %s (%s) %s",
            i, len(sample), ident, c["year"], c["document_type"][:40]
        )
        payload = client.download_pdf(c["file_download_uri"])
        text, report = _try_extract(payload)
        if text is not None:
            text_bytes = text.encode("utf-8")
            storage.save_blob(Stage.DECISION_TEXTS.value, ident, "txt", text_bytes)
            size = len(text_bytes)
        else:
            size = 0
        time.sleep(args.rate_sleep)
        enriched.append({**c, "size_bytes": size, "extract": report})

    manifest_path.write_text(json.dumps(enriched, indent=2))
    logger.info("Wrote sample manifest → %s", manifest_path)

    n_ok = sum(1 for e in enriched if e["extract"].get("ok"))
    n_text = sum(1 for e in enriched if e["extract"].get("n_chars", 0) > 1000)
    print(f"sample: {len(enriched)} FWDs")
    print(f"  pdfplumber extract ok: {n_ok}/{len(enriched)}")
    print(f"  text-extractable (>1000 chars): {n_text}/{len(enriched)}")


if __name__ == "__main__":
    main()
