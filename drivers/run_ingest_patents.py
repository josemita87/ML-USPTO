"""Stage 3 driver — fetch application file wrappers, write `Frame.PATENTS`.

Loads `Frame.TRIALS`, deduplicates `application_number`, fetches file
wrappers in `/applications/search` batches, assembles each via
`parse.patents.to_flat_record` (scalar metadata + parallel-array columns
for events/assignments/parent_continuity/CPC). The features stage
consumes those array columns off the joined frame and applies T₀
leakage filtering in pandas — no raw-cache reads after parse.

Per-application raw wrappers are cached under bucket `Stage.PATENTS`,
so reruns resume cheaply.

Failed fetches (404, 5xx, transport error, missing record) are logged and
skipped — no quarantine frame, no negative cache. The next cron run retries
each missing app automatically; over the 5M/wk metadata budget, retrying
~50 stable failures weekly is noise.

Use `--max-apps N` for smoke runs.
"""

import argparse
import logging

import pandas as pd

from ml_uspto.clients.storage import get_storage
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_patents
from ml_uspto.parse.patents import to_flat_record
from ml_uspto.schemas.enums import Frame
from ml_uspto.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-apps", type=int, default=None, help="cap application fetches")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="applications per /applications/search request; defaults to settings api.page_size",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    storage = get_storage()
    trials = storage.load_frame(Frame.TRIALS, columns=["application_number"])
    apps_series = trials["application_number"].dropna().astype(str).str.strip()
    apps = list(dict.fromkeys(apps_series[apps_series != ""]))

    rows: list[dict] = []
    n_failed = 0
    batch_size = args.batch_size or get_settings().api.page_size
    for result in fetch_patents(
        storage,
        USPTOClient(),
        iter(apps),
        page_size=batch_size,
        max_apps=args.max_apps,
    ):
        if result.raw_record is not None:
            rows.append(to_flat_record(result.raw_record))
        else:
            n_failed += 1

    patents_df = pd.DataFrame(rows)
    storage.save_frame(patents_df, Frame.PATENTS)

    attempted = len(rows) + n_failed
    print(f"apps attempted: {attempted} / {len(apps)} unique")
    print(f"batch size: {batch_size}")
    print(f"patents: {len(patents_df)} rows -> frame {Frame.PATENTS.value}")
    print(f"failed: {n_failed}")


if __name__ == "__main__":
    main()
