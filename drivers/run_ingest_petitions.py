"""Stage 2 driver — corpus-wide petition scan + assembler.

Chains:
  1. `fetch_petitions(client)` — paginate `documents/search` filtered to
     `documentCategory IN ["PETITION","Paper"]`, page-cached under
     `data/raw/documents_petition_scan/`. Yields raw rows lazily.
  2. `assemble_petitions(raw, trials)` — group by `trialNumber`, run
     `pick_petition`, emit `Petition` rows + `QuarantineEntry` rows.

Writes `petitions.parquet` and `petition_quarantine.parquet`. Requires
`trials.parquet` from stage 1.
"""

import argparse
import logging

import pandas as pd

from ml_uspto import paths
from ml_uspto.clients import local
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_petitions
from ml_uspto.parse.petition_assembler import assemble_petitions
from ml_uspto.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=None, help="cap pagination for smoke runs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    trials = local.load_parquet(
        paths.trials_parquet(),
        columns=["trial_number", "petition_filing_date"],
    )
    raw = fetch_petitions(
        USPTOClient(),
        page_size=get_settings().api.page_size,
        max_pages=args.max_pages,
    )
    petitions, quarantine = assemble_petitions(raw, trials)

    petitions_df = pd.DataFrame([p.model_dump() for p in petitions])
    quarantine_df = pd.DataFrame([q.model_dump() for q in quarantine])

    petitions_path = paths.petitions_parquet()
    quarantine_path = paths.petition_quarantine_parquet()
    local.save_parquet(petitions_df, petitions_path)
    local.save_parquet(quarantine_df, quarantine_path)

    print(f"petitions: {len(petitions_df)} rows → {petitions_path}")
    print(f"quarantine: {len(quarantine_df)} rows → {quarantine_path}")


if __name__ == "__main__":
    main()
