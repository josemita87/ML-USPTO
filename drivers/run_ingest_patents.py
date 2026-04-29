"""Stage 3 driver — fetch application file wrappers, write patent parquet outputs.

Loads `trials.parquet`, deduplicates `application_number`, fetches file
wrappers in `/applications/search` batches, then writes:

  - `patents.parquet` — static-only flatten via `Parser.PATENTS`
  - `patent_quarantine.parquet` — applications not returned or otherwise unusable

Use `--max-apps N` for smoke runs. Per-application raw wrappers are cached
under `data/raw/patents/{application_number}.json`, so reruns resume cheaply.
"""

import argparse
import logging

import pandas as pd

from ml_uspto import paths
from ml_uspto.clients import local
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_patents
from ml_uspto.parse.engine import flatten, load_parser_config
from ml_uspto.parse.schemas.enums import Parser
from ml_uspto.schemas.models import PatentQuarantineEntry
from ml_uspto.settings import get_settings


def _application_numbers(trials: pd.DataFrame) -> list[str]:
    apps = trials["application_number"].dropna().astype(str).str.strip()
    apps = apps[apps != ""]
    return list(dict.fromkeys(apps))


def _empty_patents_frame() -> pd.DataFrame:
    columns = list(load_parser_config(Parser.PATENTS)["columns"])
    return pd.DataFrame(columns=columns)


def _empty_quarantine_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(PatentQuarantineEntry.model_fields))


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

    trials = local.load_parquet(paths.trials_parquet(), columns=["application_number"])
    apps = _application_numbers(trials)

    records: list[dict] = []
    quarantine: list[PatentQuarantineEntry] = []
    batch_size = args.batch_size or get_settings().api.page_size
    for result in fetch_patents(
        USPTOClient(),
        iter(apps),
        page_size=batch_size,
        max_apps=args.max_apps,
    ):
        if result.raw_record is not None:
            records.append(result.raw_record)
        if result.quarantine is not None:
            quarantine.append(result.quarantine)

    patents_df = flatten(records, Parser.PATENTS) if records else _empty_patents_frame()
    quarantine_df = (
        pd.DataFrame([q.model_dump(mode="json") for q in quarantine])
        if quarantine
        else _empty_quarantine_frame()
    )

    patents_path = paths.patents_parquet()
    quarantine_path = paths.patent_quarantine_parquet()
    local.save_parquet(patents_df, patents_path)
    local.save_parquet(quarantine_df, quarantine_path)

    attempted = len(records) + len(quarantine)
    print(f"apps attempted: {attempted} / {len(apps)} unique")
    print(f"batch size: {batch_size}")
    print(f"patents: {len(patents_df)} rows -> {patents_path}")
    print(f"quarantine: {len(quarantine_df)} rows -> {quarantine_path}")
    if attempted:
        print(f"quarantine rate: {len(quarantine_df) / attempted:.1%}")


if __name__ == "__main__":
    main()
