"""Step 2: Preprocess raw data and create target labels."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.preprocessing import preprocess
from src.settings import get_settings
from src.utils.io import load_parquet, resolve_path, save_parquet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    settings = get_settings()
    settings.ensure_dirs()

    raw_path = resolve_path(settings.data.raw_dir / "ipr_proceedings.parquet")
    df = load_parquet(raw_path)
    logger.info("Loaded %d raw records", len(df))

    df = preprocess(df)

    out_path = resolve_path(settings.data.processed_dir / "ipr_clean.parquet")
    save_parquet(df, out_path)
    logger.info("Saved %d preprocessed records to %s", len(df), out_path)


if __name__ == "__main__":
    main()
