"""Stage 5 driver — backfill FWD texts whose label can't be resolved from
`trial_status` or `documentTitleText`.

Runs the same code on cold start (~1.1k candidates today) and weekly cron
(~30–50 deltas as new FWDs issue): `enumerate_missing_fwd_pdfs` derives the
candidate list freshly each time from `Frame.TRIALS` + the raw
`Stage.DECISIONS` cache + `iter_blob_keys` lookup against `Stage.DECISION_TEXTS`.
There is no persistent manifest beyond the cache itself. Each successful
fetch downloads the PDF, extracts full text via pdfplumber, and persists
the text only — the binary is never written to disk. Failed downloads are
logged and silently retried on the next cron pass; at ~30–50 weekly
candidates and a 1.2M/wk PDF budget, weekly retries of permanently-broken
docs are negligible.

Flags:
  --dry-run     : run the gap-detector and print count + sample, no fetch.
  --limit N     : cap downloads (smoke runs / `--limit 50` re-run idempotency check).
  --rate-sleep S: inter-request pause to stay under the ~1.2M/wk PDF bucket budget (default 2.0s).
"""

import argparse
import logging

from ml_uspto.clients.storage import get_storage
from ml_uspto.ingest.fetch import fetch_decision_pdfs
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.decisions import enumerate_missing_fwd_pdfs
from ml_uspto.ingest.schemas.enums import FwdPdfCandidateColumn as Col
from ml_uspto.schemas.enums import Frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="cap downloads")
    parser.add_argument("--rate-sleep", type=float, default=2.0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    storage = get_storage()
    trials = storage.load_frame(Frame.TRIALS)

    candidates = enumerate_missing_fwd_pdfs(storage, trials=trials)

    print(f"candidates: {len(candidates)}")
    if candidates.empty:
        return

    if args.dry_run:
        head = candidates.head(5)[
            [Col.TRIAL_NUMBER.value, Col.DOCUMENT_IDENTIFIER.value, Col.DECISION_ISSUE_DATE.value]
        ]
        print(head.to_string(index=False))
        return

    if args.limit is not None:
        candidates = candidates.head(args.limit)
        print(f"capped to: {len(candidates)} (--limit)")

    client = USPTOClient()
    n_ok = 0
    n_fail = 0
    for result in fetch_decision_pdfs(
        storage, client, candidates, rate_sleep=args.rate_sleep
    ):
        if result.bytes_written is None:
            n_fail += 1
        else:
            n_ok += 1

    print(f"downloaded: {n_ok}")
    print(f"failed: {n_fail}")


if __name__ == "__main__":
    main()
