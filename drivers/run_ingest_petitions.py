"""Stage 2 driver — corpus-wide petition scan + assembler.

Chains:
  1. `fetch_petitions(storage, client)` — paginate `documents/search`
     filtered to `documentCategory IN ["PETITION","Paper"]`, page-cached
     under bucket `Stage.DOCUMENTS_PETITION_SCAN`. Yields raw rows lazily.
  2. `assemble_petitions(raw, trials)` — group by `trialNumber`, run
     `pick_petition`, emit `Petition` rows + `QuarantineEntry` rows.

Saves `Frame.PETITIONS` and `Frame.PETITION_QUARANTINE`. Requires
`Frame.TRIALS` from stage 1.
"""

import argparse
import logging

import pandas as pd

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_petitions
from ml_uspto.parse.petitions import assemble_petitions
from ml_uspto.schemas.enums import Frame
from ml_uspto.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=None, help="cap pagination for smoke runs")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    storage = LocalStorage()
    trials = storage.load_frame(
        Frame.TRIALS,
        columns=["trial_number", "petition_filing_date"],
    )
    raw = fetch_petitions(
        storage,
        USPTOClient(),
        page_size=get_settings().api.page_size,
        max_pages=args.max_pages,
    )
    petitions, quarantine = assemble_petitions(raw, trials)

    petitions_df = pd.DataFrame([p.model_dump() for p in petitions])
    quarantine_df = pd.DataFrame([q.model_dump() for q in quarantine])

    storage.save_frame(petitions_df, Frame.PETITIONS)
    storage.save_frame(quarantine_df, Frame.PETITION_QUARANTINE)

    print(f"petitions: {len(petitions_df)} rows → frame {Frame.PETITIONS.value}")
    print(f"quarantine: {len(quarantine_df)} rows → frame {Frame.PETITION_QUARANTINE.value}")


if __name__ == "__main__":
    main()
