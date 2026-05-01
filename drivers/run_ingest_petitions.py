"""Stage 2 driver — corpus-wide petition scan + assembler.

Chains:
  1. `fetch_petitions(storage, client)` — paginate `documents/search`
     filtered to `documentCategory IN ["PETITION","Paper"]`, page-cached
     under bucket `Stage.DOCUMENTS_PETITION_SCAN`. Yields raw rows lazily.
  2. `assemble_petitions(raw)` — group by `trialNumber`, run
     `pick_petition`, emit `Petition` rows. Trials with no pickable
     petition are silently dropped; the joiner reconciles them via
     inner-join with the labeled trials.

Saves `Frame.PETITIONS`. The trial roster is *not* read here.
"""

import argparse
import logging

import pandas as pd

from ml_uspto.clients.storage import get_storage
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

    storage = get_storage()
    raw = fetch_petitions(
        storage,
        USPTOClient(),
        page_size=get_settings().api.page_size,
        max_pages=args.max_pages,
    )
    petitions = assemble_petitions(raw)
    petitions_df = pd.DataFrame([p.model_dump() for p in petitions])

    storage.save_frame(petitions_df, Frame.PETITIONS)
    print(f"petitions: {len(petitions_df)} rows → frame {Frame.PETITIONS.value}")


if __name__ == "__main__":
    main()
