"""Fetch IPR proceedings/decisions from USPTO and flatten via declarative YAML.

The flattening rules live in `config/parsers/{proceedings,decisions}.yaml`,
not here, so adding fields or surfaces does not require code changes.
"""

import logging

import pandas as pd

from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.parse.engine import flatten

logger = logging.getLogger(__name__)


def _paginate(
    fetch_page,
    *,
    records_key: str,
    max_pages: int,
    page_size: int,
    label: str,
) -> list[dict]:
    all_records: list[dict] = []
    offset = 0
    for page in range(max_pages):
        logger.info("Fetching %s page %d (offset=%d)", label, page + 1, offset)
        data = fetch_page(query="IPR", offset=offset, limit=page_size)
        records = data.get(records_key, [])
        if not records:
            logger.info("No more %s records at offset %d", label, offset)
            break
        all_records.extend(records)
        offset += page_size
        total = data.get("count", 0)
        if offset >= total:
            break
    logger.info("Fetched %d total %s", len(all_records), label)
    return all_records


def fetch_ipr_proceedings(
    client: USPTOClient, max_pages: int = 50, page_size: int = 100
) -> pd.DataFrame:
    records = _paginate(
        client.search_proceedings,
        records_key="patentTrialProceedingDataBag",
        max_pages=max_pages,
        page_size=page_size,
        label="proceedings",
    )
    return flatten(records, "proceedings")


def fetch_ipr_decisions(
    client: USPTOClient, max_pages: int = 50, page_size: int = 100
) -> pd.DataFrame:
    records = _paginate(
        client.search_decisions,
        records_key="patentTrialDocumentDataBag",
        max_pages=max_pages,
        page_size=page_size,
        label="decisions",
    )
    return flatten(records, "decisions")
