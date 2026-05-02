"""Stage 5/6 driver — fetch petition PDFs → cache text blobs → assemble `Frame.PETITION_TEXTS`."""

import argparse
import logging

from ml_uspto.clients.storage import get_storage
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_petition_pdfs
from ml_uspto.ingest.petitions import (
    build_petition_texts_frame,
    enumerate_missing_petition_pdfs,
)
from ml_uspto.schemas.enums import Frame


def main() -> None:
    """Single-pass petition-text ingest: fetch missing PDFs, then rebuild the text frame.

    Cold start (~17.8K rows, ~70 GB transferred, ~10 h serial) and
    weekly cron deltas run the same code path:
      1. ``enumerate_missing_petition_pdfs`` derives the candidate list
         each invocation from ``Frame.PETITIONS`` plus an
         ``iter_blob_keys`` lookup against ``Stage.PETITION_TEXTS``.
      2. ``fetch_petition_pdfs`` downloads each candidate PDF, runs
         pdfplumber, and persists only the text — the binary is never
         written to disk (mirrors the FWD policy).
      3. ``build_petition_texts_frame`` walks the (now refreshed) blob
         store and assembles ``Frame.PETITION_TEXTS`` ready for the
         joiner to left-join onto labeled trials.

    Failed downloads are logged and silently retried on the next pass.
    The text frame is rebuilt from the full blob set on every run, so
    feature engineering against this frame is automatically up-to-date.

    Flags:
        --dry-run: run the gap detector, print the count and a sample;
            no fetch and no frame rebuild.
        --limit N: cap downloads (smoke runs / re-run idempotency
            checks).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="cap downloads")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    storage = get_storage()
    petitions = storage.load_frame(Frame.PETITIONS)

    candidates = enumerate_missing_petition_pdfs(storage, petitions=petitions)
    print(f"candidates: {len(candidates)}")

    if args.dry_run:
        if not candidates.empty:
            print(candidates.head(5).to_string(index=False))
        return

    if args.limit is not None and not candidates.empty:
        candidates = candidates.head(args.limit)
        print(f"capped to: {len(candidates)} (--limit)")

    if not candidates.empty:
        client = USPTOClient()
        n_ok = 0
        n_fail = 0
        for result in fetch_petition_pdfs(storage, client, candidates):
            if result.bytes_written is None:
                n_fail += 1
            else:
                n_ok += 1
        print(f"downloaded: {n_ok}")
        print(f"failed: {n_fail}")

    df = build_petition_texts_frame(storage)
    storage.save_frame(df, Frame.PETITION_TEXTS)
    print(f"wrote -> frame {Frame.PETITION_TEXTS.value} ({len(df)} rows)")


if __name__ == "__main__":
    main()
