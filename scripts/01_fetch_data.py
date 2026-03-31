"""Step 1: Fetch IPR proceedings from the USPTO API."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.client import USPTOClient
from src.data.fetch import fetch_ipr_proceedings
from src.settings import get_settings
from src.utils.io import resolve_path, save_parquet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    settings = get_settings()
    settings.ensure_dirs()

    client = USPTOClient()
    df = fetch_ipr_proceedings(
        client,
        max_pages=settings.api.max_pages,
        page_size=settings.api.page_size,
    )

    out_path = resolve_path(settings.data.raw_dir / "ipr_proceedings.parquet")
    save_parquet(df, out_path)
    logger.info("Saved %d raw records to %s", len(df), out_path)


if __name__ == "__main__":
    main()
