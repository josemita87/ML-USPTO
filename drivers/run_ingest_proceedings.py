"""Stage 1 driver — fetch IPR proceedings, write `data/processed/trials.parquet`.

Resumable: page-by-page cache under `data/raw/proceedings/` is consulted
before any HTTP call, so re-running picks up where Ctrl-C left off.
`--max-pages N` caps the loop for smoke runs.
"""

import argparse
import logging

from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_proceedings
from ml_uspto.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, default=None, help="cap pagination for smoke runs")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    df = fetch_proceedings(
        USPTOClient(),
        page_size=get_settings().api.page_size,
        max_pages=args.max_pages,
    )
    print(f"trials: {len(df)} rows")


if __name__ == "__main__":
    main()
