"""Stage 5 driver — backfill FWD PDFs whose label can't be resolved from
`trial_status` or `documentTitleText`, append failure rows for retry.

Runs the same code on cold start (~1.1k candidates today) and weekly cron
(~30–50 deltas as new FWDs issue): `enumerate_missing_fwd_pdfs` derives the
candidate list freshly each time from `Frame.TRIALS` + the raw
`Stage.DECISIONS` cache + `has_blob` checks against `Stage.DECISION_PDFS` +
the `Frame.DECISION_PDF_FAILURES` parquet. There is no persistent manifest
beyond the cache itself.

Flags:
  --dry-run           : run the gap-detector and print count + sample, no fetch.
  --limit N           : cap downloads (smoke runs / `--limit 50` re-run idempotency check).
  --retry-after-days N: skip docs whose latest failure is within N days (default 7).
  --rate-sleep S      : inter-request pause to stay under the ~1.2M/wk PDF bucket budget (default 2.0s).
"""

import argparse
import logging

import pandas as pd

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.protocols.storage import Storage
from ml_uspto.ingest.fetch import fetch_decision_pdfs
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.parse.decisions import enumerate_missing_fwd_pdfs
from ml_uspto.parse.schemas.enums import FwdPdfCandidateColumn as Col
from ml_uspto.schemas.enums import Frame
from ml_uspto.schemas.models import DecisionPdfFailure


def _load_failures(storage: Storage) -> pd.DataFrame:
    try:
        return storage.load_frame(Frame.DECISION_PDF_FAILURES)
    except FileNotFoundError:
        return pd.DataFrame(columns=list(DecisionPdfFailure.model_fields))


def _persist_failures(
    storage: Storage, prior: pd.DataFrame, new_rows: list[DecisionPdfFailure]
) -> None:
    if not new_rows:
        return
    new_df = pd.DataFrame([f.model_dump(mode="json") for f in new_rows])
    combined = pd.concat([prior, new_df], ignore_index=True) if not prior.empty else new_df
    storage.save_frame(combined, Frame.DECISION_PDF_FAILURES)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="cap downloads")
    parser.add_argument("--retry-after-days", type=int, default=7)
    parser.add_argument("--rate-sleep", type=float, default=2.0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    storage = LocalStorage()
    trials = storage.load_frame(Frame.TRIALS)
    failures = _load_failures(storage)

    candidates = enumerate_missing_fwd_pdfs(
        storage,
        trials=trials,
        failures=failures,
        retry_after_days=args.retry_after_days,
    )

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
    new_failures: list[DecisionPdfFailure] = []
    n_ok = 0
    try:
        for result in fetch_decision_pdfs(
            storage, client, candidates, rate_sleep=args.rate_sleep
        ):
            if result.failure is not None:
                new_failures.append(result.failure)
            else:
                n_ok += 1
    finally:
        _persist_failures(storage, failures, new_failures)

    print(f"downloaded: {n_ok}")
    print(f"failed: {len(new_failures)}")
    if new_failures:
        by_reason: dict[str, int] = {}
        for f in new_failures:
            by_reason[f.reason.value] = by_reason.get(f.reason.value, 0) + 1
        for r, n in sorted(by_reason.items()):
            print(f"  {r}: {n}")
        print(f"failures frame: {Frame.DECISION_PDF_FAILURES.value}")


if __name__ == "__main__":
    main()
