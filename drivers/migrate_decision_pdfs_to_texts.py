"""One-shot migration — convert legacy `data/raw/decision_pdfs/*.pdf` to
`data/raw/decision_texts/*.txt` via pdfplumber.

Run once after the decision-text refactor (2026-04-30+). Idempotent: skips
documents whose `.txt` already exists. Leaves the source `.pdf` files in
place so the migration is reversible until you delete them by hand. The
sample manifest (if present) is copied across.

Usage:
    uv run python drivers/migrate_decision_pdfs_to_texts.py [--delete-pdfs]
"""

from __future__ import annotations

import argparse
import io
import logging
import shutil
from pathlib import Path

import pdfplumber

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.ingest.schemas.enums import Stage

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete-pdfs",
        action="store_true",
        help="Remove source .pdf files after successful conversion",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    storage = LocalStorage()
    src_dir = storage.raw_root / "decision_pdfs"
    dst_dir = storage.raw_root / Stage.DECISION_TEXTS.value

    if not src_dir.exists():
        logger.info("No legacy %s dir; nothing to migrate", src_dir)
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    src_manifest = src_dir / "_sample_manifest.json"
    dst_manifest = dst_dir / "_sample_manifest.json"
    if src_manifest.exists() and not dst_manifest.exists():
        shutil.copy2(src_manifest, dst_manifest)
        logger.info("Copied sample manifest → %s", dst_manifest)

    pdfs = sorted(p for p in src_dir.glob("*.pdf"))
    logger.info("Found %d legacy PDFs in %s", len(pdfs), src_dir)

    n_converted = 0
    n_skipped = 0
    n_failed = 0
    for i, pdf_path in enumerate(pdfs, 1):
        doc_id = pdf_path.stem
        if storage.has_blob(Stage.DECISION_TEXTS.value, doc_id, "txt"):
            n_skipped += 1
            continue
        try:
            with pdfplumber.open(io.BytesIO(pdf_path.read_bytes())) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed %s: %s", doc_id, exc)
            n_failed += 1
            continue
        storage.save_blob(Stage.DECISION_TEXTS.value, doc_id, "txt", text.encode("utf-8"))
        n_converted += 1
        if args.delete_pdfs:
            pdf_path.unlink()
        if i % 50 == 0:
            logger.info("[%d/%d] converted=%d skipped=%d failed=%d",
                        i, len(pdfs), n_converted, n_skipped, n_failed)

    print(f"converted: {n_converted}")
    print(f"skipped (already cached): {n_skipped}")
    print(f"failed: {n_failed}")
    if args.delete_pdfs and n_converted:
        print(f"deleted {n_converted} source PDFs from {src_dir}")
    else:
        print(f"source PDFs preserved at {src_dir} — re-run with --delete-pdfs to remove")


if __name__ == "__main__":
    main()
