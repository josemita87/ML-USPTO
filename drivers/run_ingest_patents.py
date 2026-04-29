"""Stage 3 driver — fetch application file wrappers, write patent frames.

Loads the `Frame.TRIALS` frame, deduplicates `application_number`, fetches
file wrappers in `/applications/search` batches, then writes:

  - `Frame.PATENTS` — static-only flatten via `Parser.PATENTS`
  - `Frame.PATENT_QUARANTINE` — applications not returned or otherwise unusable

Use `--max-apps N` for smoke runs. Per-application raw wrappers are cached
under bucket `Stage.PATENTS`, so reruns resume cheaply.
"""

import argparse
import logging

import pandas as pd

from ml_uspto.clients.local import LocalStorage
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_patents
from ml_uspto.parse.engine import flatten, load_parser_config
from ml_uspto.parse.schemas.enums import Parser
from ml_uspto.schemas.enums import Frame
from ml_uspto.schemas.models import PatentQuarantineEntry
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

    storage = LocalStorage()
    trials = storage.load_frame(Frame.TRIALS, columns=["application_number"])
    apps_series = trials["application_number"].dropna().astype(str).str.strip()
    apps = list(dict.fromkeys(apps_series[apps_series != ""]))

    records: list[dict] = []
    quarantine: list[PatentQuarantineEntry] = []
    batch_size = args.batch_size or get_settings().api.page_size
    for result in fetch_patents(
        storage,
        USPTOClient(),
        iter(apps),
        page_size=batch_size,
        max_apps=args.max_apps,
    ):
        if result.raw_record is not None:
            records.append(result.raw_record)
        if result.quarantine is not None:
            quarantine.append(result.quarantine)

    patents_df = (
        flatten(records, Parser.PATENTS)
        if records
        else pd.DataFrame(columns=list(load_parser_config(Parser.PATENTS)["columns"]))
    )
    quarantine_df = (
        pd.DataFrame([q.model_dump(mode="json") for q in quarantine])
        if quarantine
        else pd.DataFrame(columns=list(PatentQuarantineEntry.model_fields))
    )

    storage.save_frame(patents_df, Frame.PATENTS)
    storage.save_frame(quarantine_df, Frame.PATENT_QUARANTINE)

    attempted = len(records) + len(quarantine)
    print(f"apps attempted: {attempted} / {len(apps)} unique")
    print(f"batch size: {batch_size}")
    print(f"patents: {len(patents_df)} rows -> frame {Frame.PATENTS.value}")
    print(f"quarantine: {len(quarantine_df)} rows -> frame {Frame.PATENT_QUARANTINE.value}")
    if attempted:
        print(f"quarantine rate: {len(quarantine_df) / attempted:.1%}")


if __name__ == "__main__":
    main()
