"""Stage 1b driver — fetch IPR decisions, write `data/processed/decisions.parquet`.

The decisions surface is required to derive the `cancelled` label for trials
whose `trial_status` is `Final Written Decision[ - Appealed]` (~6K of ~17.5K
labelable trials). For non-FWD-status trials the label is fully determined by
`trial_status` and decisions data is unused. Resumable like the proceedings
driver: page-by-page cache under `data/raw/decisions/`.
"""

import argparse
import logging

from ml_uspto.clients.storage.local import LocalStorage
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_decisions
from ml_uspto.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=None, help="cap pagination for smoke runs")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    df = fetch_decisions(
        LocalStorage(),
        USPTOClient(),
        page_size=get_settings().api.page_size,
        max_pages=args.max_pages,
    )
    print(f"decisions: {len(df)} rows")


if __name__ == "__main__":
    main()
