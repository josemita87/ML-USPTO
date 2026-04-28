"""Stages 1 + 2 of the ingestion pipeline — paginate, cache, flatten.

Two entrypoints, one shared paginator. Pages are cached page-by-page
under `data/raw/<stage>/page_NNNN.json` via `clients.local`, so a
re-run after Ctrl-C resumes from the last cached page without
re-issuing requests. The same key shape maps 1:1 to S3 when the
backend swaps (see `clients/local.py`).

  - `fetch_proceedings(client)` → POST `/trials/proceedings/search`
    filtered to `trialTypeCode = "IPR"`, flattened with
    `Parser.PROCEEDINGS`, written to `paths.trials_parquet()`.
  - `fetch_petitions(client)` → POST `/trials/documents/search` filtered
    to `documentData.documentCategory IN PETITION_SCAN_CATEGORIES` (set
    in `config/petition_picker.yaml` — currently PETITION + Paper per
    `docs/api/proceedings.md` "Petition coverage and the category-taxonomy
    drift"). Yields raw records lazily so the assembler streams without
    holding the full ~323K-row corpus in memory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import pandas as pd

from ml_uspto import paths
from ml_uspto.clients import local
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.schemas.constants import PETITION_SCAN_CATEGORIES, STAGE_RECORDS_KEY
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.engine import flatten
from ml_uspto.parse.schemas.enums import Parser

logger = logging.getLogger(__name__)


def _paginate_cached(
    client_method: Callable[..., dict],
    *,
    filters: list[dict],
    bucket: Stage,
    page_size: int,
    max_pages: int | None = None,
) -> Iterator[dict]:
    """Yield records page-by-page, caching each response under `bucket`.

    Every `USPTOClient.search_*_post` shares the
    `(filters, offset, limit) -> dict` signature, so we bind the surface
    here. The full payload is cached (not just the records) so `count`
    survives resumes. The records-bag key is looked up from `bucket`
    via `STAGE_RECORDS_KEY` — callers don't pass it.

    `max_pages` caps the loop for smoke runs; the cap stops the loop
    *before* fetching, so already-cached pages past the cap are not
    consumed even though they exist on disk.
    """
    records_key = STAGE_RECORDS_KEY[bucket]
    pages_done = 0
    offset = 0
    yielded = 0
    while True:
        if max_pages is not None and pages_done >= max_pages:
            logger.info("Stopping %s at page %d (max_pages cap)", bucket.value, pages_done)
            break
        key = f"page_{pages_done:04d}"
        payload = local.load_if_present(bucket, key)
        if payload is None:
            logger.info("Fetching %s page %d (offset=%d)", bucket.value, pages_done, offset)
            payload = client_method(filters=filters, offset=offset, limit=page_size)
            local.save(bucket, key, payload)
        else:
            logger.debug("%s page %d cache hit", bucket.value, pages_done)

        records = payload.get(records_key) or []
        if not records:
            break
        yield from records
        yielded += len(records)
        pages_done += 1

        offset += page_size
        total = payload.get("count")
        if isinstance(total, int) and offset >= total:
            break

    logger.info("Fetched %d %s records (%d pages)", yielded, bucket.value, pages_done)


def fetch_proceedings(
    client: USPTOClient, *, page_size: int, max_pages: int | None = None
) -> pd.DataFrame:
    """Fetch all IPR proceedings, flatten, and write `trials.parquet`.

    Filters via POST on `trialMetaData.trialTypeCode = "IPR"`. The
    flattened columns come from `Parser.PROCEEDINGS` in
    `config/parsers/patents.yaml`. Caller passes `page_size` explicitly
    (typically `get_settings().api.page_size`) — no in-function defaults.
    """
    records = _paginate_cached(
        client.search_proceedings_post,
        filters=[{"name": "trialMetaData.trialTypeCode", "value": ["IPR"]}],
        bucket=Stage.PROCEEDINGS,
        page_size=page_size,
        max_pages=max_pages,
    )
    df = flatten(records, Parser.PROCEEDINGS)
    out = paths.trials_parquet()
    local.save_parquet(df, out)
    logger.info("Wrote %d proceedings rows → %s", len(df), out)
    return df


def fetch_petitions(
    client: USPTOClient, *, page_size: int, max_pages: int | None = None
) -> Iterator[dict]:
    """Yield raw document records from the corpus-wide petition scan.

    Filter is `documentData.documentCategory IN PETITION_SCAN_CATEGORIES`
    (currently PETITION + Paper per `config/petition_picker.yaml` and
    `docs/api/proceedings.md`); both buckets must be scanned because
    pre-2022 petitions live in the `Paper` catch-all. The `OTHER` bucket
    is intentionally excluded — see the YAML comment for the empirical
    rationale. Records are cached page-by-page under
    `data/raw/documents_petition_scan/`. The iterator is single-pass
    (one page resident at a time); pass it directly to
    `parse.petition_assembler.assemble_petitions` to avoid materializing
    all ~323K records.
    """
    return _paginate_cached(
        client.search_documents_post,
        filters=[{"name": "documentData.documentCategory", "value": PETITION_SCAN_CATEGORIES}],
        bucket=Stage.DOCUMENTS_PETITION_SCAN,
        page_size=page_size,
        max_pages=max_pages,
    )


__all__ = ["fetch_proceedings", "fetch_petitions"]
