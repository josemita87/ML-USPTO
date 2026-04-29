"""Ingestion fetchers — paginate/search, cache, flatten.

Two entrypoints, one shared paginator. Pages and per-app payloads are
cached object-by-object through the injected `Storage` backend
(`clients.storage.Storage`), so a re-run after Ctrl-C resumes from the
last cached object without re-issuing requests. `LocalStorage` puts
them under `data/raw/<bucket>/<key>.json`; an S3 backend uses the same
`(bucket, key)` shape against `s3://...`.

  - `fetch_proceedings(storage, client)` → POST `/trials/proceedings/search`
    filtered to `trialTypeCode = "IPR"`, flattened with
    `Parser.PROCEEDINGS`, saved as the `Frame.TRIALS` frame.
  - `fetch_petitions(storage, client)` → POST `/trials/documents/search`
    filtered to `documentData.documentCategory IN PETITION_SCAN_CATEGORIES`
    (set in `config/petition_picker.yaml` — currently PETITION + Paper per
    `docs/api/proceedings.md` "Petition coverage and the category-taxonomy
    drift"). Yields raw records lazily so the assembler streams without
    holding the full ~323K-row corpus in memory.
  - `fetch_patents(storage, client, application_numbers)` → POST
    `/applications/search` in application-number batches, cache each
    returned wrapper under bucket `Stage.PATENTS`, and yield per-app
    success/quarantine outcomes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import pandas as pd
import requests

from ml_uspto.clients.storage import Storage
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.schemas.constants import PETITION_SCAN_CATEGORIES, STAGE_RECORDS_KEY
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.parse.engine import flatten
from ml_uspto.parse.schemas.enums import Parser
from ml_uspto.schemas.enums import Frame, PatentQuarantineReason
from ml_uspto.schemas.models import PatentFetchResult, PatentQuarantineEntry

logger = logging.getLogger(__name__)


def _paginate_cached(
    storage: Storage,
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
        payload = storage.load_object(bucket, key)
        if payload is None:
            logger.info("Fetching %s page %d (offset=%d)", bucket.value, pages_done, offset)
            payload = client_method(filters=filters, offset=offset, limit=page_size)
            storage.save_object(bucket, key, payload)
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
    storage: Storage,
    client: USPTOClient,
    *,
    page_size: int,
    max_pages: int | None = None,
) -> pd.DataFrame:
    """Fetch all IPR proceedings, flatten, and save `Frame.TRIALS`.

    Filters via POST on `trialMetaData.trialTypeCode = "IPR"`. The
    flattened columns come from `Parser.PROCEEDINGS` in
    `config/parsers/patents.yaml`. Caller passes `page_size` explicitly
    (typically `get_settings().api.page_size`) — no in-function defaults.
    """
    records = _paginate_cached(
        storage,
        client.search_proceedings_post,
        filters=[{"name": "trialMetaData.trialTypeCode", "value": ["IPR"]}],
        bucket=Stage.PROCEEDINGS,
        page_size=page_size,
        max_pages=max_pages,
    )
    df = flatten(records, Parser.PROCEEDINGS)
    storage.save_frame(df, Frame.TRIALS)
    logger.info("Wrote %d proceedings rows → frame %s", len(df), Frame.TRIALS.value)
    return df


def fetch_decisions(
    storage: Storage,
    client: USPTOClient,
    *,
    page_size: int,
    max_pages: int | None = None,
) -> pd.DataFrame:
    """Fetch all IPR decisions, flatten, and save `Frame.DECISIONS`.

    Filters via POST on `trialMetaData.trialTypeCode = "IPR"`. Same paginator
    + cache plumbing as `fetch_proceedings`; pages cached under bucket
    `Stage.DECISIONS`. Required to derive the `cancelled` label for trials
    with `Final Written Decision` status — `parse.preprocessing` looks up the
    terminating FWD's `trialOutcomeCategory` per trial.
    """
    records = list(
        _paginate_cached(
            storage,
            client.search_decisions_post,
            filters=[{"name": "trialMetaData.trialTypeCode", "value": ["IPR"]}],
            bucket=Stage.DECISIONS,
            page_size=page_size,
            max_pages=max_pages,
        )
    )
    df = flatten(records, Parser.DECISIONS)
    storage.save_frame(df, Frame.DECISIONS)
    logger.info("Wrote %d decisions rows → frame %s", len(df), Frame.DECISIONS.value)
    return df


def fetch_petitions(
    storage: Storage,
    client: USPTOClient,
    *,
    page_size: int,
    max_pages: int | None = None,
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
        storage,
        client.search_documents_post,
        filters=[{"name": "documentData.documentCategory", "value": PETITION_SCAN_CATEGORIES}],
        bucket=Stage.DOCUMENTS_PETITION_SCAN,
        page_size=page_size,
        max_pages=max_pages,
    )


def _extract_patent_record(
    payload: dict[str, Any], application_number: str
) -> dict[str, Any] | None:
    """Return the single file-wrapper record from an application response."""
    bag = payload.get("patentFileWrapperDataBag")
    if isinstance(bag, list) and bag:
        record = bag[0]
    else:
        record = payload

    if not isinstance(record, dict) or not record:
        return None

    out = dict(record)
    out.setdefault("applicationNumberText", application_number)
    return out


def _http_quarantine(
    application_number: str, exc: requests.HTTPError, reason: PatentQuarantineReason
) -> PatentQuarantineEntry:
    response = exc.response
    status = response.status_code if response is not None else None
    return PatentQuarantineEntry(
        application_number=application_number,
        reason=reason,
        http_status=status,
        error=str(exc),
    )


def _cache_quarantine(storage: Storage, quarantine: PatentQuarantineEntry) -> None:
    storage.save_object(
        Stage.PATENTS,
        quarantine.application_number,
        {
            "applicationNumber": quarantine.application_number,
            "_fetch_error": quarantine.model_dump(mode="json"),
        },
    )


def _fetch_patent_batch(
    storage: Storage, client: USPTOClient, application_numbers: list[str]
) -> Iterator[PatentFetchResult]:
    try:
        payload = client.search_applications_post(
            filters=[{"name": "applicationNumberText", "value": application_numbers}],
            offset=0,
            limit=len(application_numbers),
        )
    except requests.HTTPError as exc:
        for app in application_numbers:
            quarantine = _http_quarantine(app, exc, PatentQuarantineReason.HTTP_ERROR)
            _cache_quarantine(storage, quarantine)
            yield PatentFetchResult(application_number=app, quarantine=quarantine)
        return
    except requests.RequestException as exc:
        for app in application_numbers:
            quarantine = PatentQuarantineEntry(
                application_number=app,
                reason=PatentQuarantineReason.REQUEST_ERROR,
                error=str(exc),
            )
            _cache_quarantine(storage, quarantine)
            yield PatentFetchResult(application_number=app, quarantine=quarantine)
        return

    records = payload.get("patentFileWrapperDataBag") or []
    records_by_app: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        app = str(record.get("applicationNumberText") or "").strip()
        if app:
            records_by_app[app] = record

    for app in application_numbers:
        record = records_by_app.get(app)
        if record is None:
            quarantine = PatentQuarantineEntry(
                application_number=app,
                reason=PatentQuarantineReason.NOT_FOUND,
                error="Application not returned by /applications/search",
            )
            _cache_quarantine(storage, quarantine)
            yield PatentFetchResult(application_number=app, quarantine=quarantine)
            continue

        payload = {"patentFileWrapperDataBag": [record]}
        storage.save_object(Stage.PATENTS, app, payload)
        yield PatentFetchResult(
            application_number=app, raw_record=_extract_patent_record(payload, app)
        )


def fetch_patents(
    storage: Storage,
    client: USPTOClient,
    application_numbers: Iterable[str],
    *,
    page_size: int,
    max_apps: int | None = None,
) -> Iterator[PatentFetchResult]:
    """Fetch application file wrappers by application number with per-app caching.

    Uncached applications are fetched in batches through `/applications/search`
    filtered by `applicationNumberText`. Successes cache one raw wrapper payload
    under bucket `Stage.PATENTS`, key=application_number; missing applications
    and request failures cache explicit fetch-error payloads so reruns do not
    repeatedly hit known-missing apps.
    """
    apps: list[str] = []
    seen: set[str] = set()
    for raw_app in application_numbers:
        app = str(raw_app).strip()
        if not app or app in seen:
            continue
        seen.add(app)
        apps.append(app)
        if max_apps is not None and len(apps) >= max_apps:
            break

    pending: list[str] = []
    for app in apps:
        payload = storage.load_object(Stage.PATENTS, app)
        if payload is None:
            pending.append(app)
            continue

        logger.debug("Patent application %s cache hit", app)
        error = payload.get("_fetch_error")
        if isinstance(error, dict):
            cached_quarantine = PatentQuarantineEntry.model_validate(error)
            yield PatentFetchResult(application_number=app, quarantine=cached_quarantine)
            continue

        record = _extract_patent_record(payload, app)
        if record is None:
            quarantine = PatentQuarantineEntry(
                application_number=app,
                reason=PatentQuarantineReason.EMPTY_RESPONSE,
                error="No patentFileWrapperDataBag record in response",
            )
            yield PatentFetchResult(application_number=app, quarantine=quarantine)
            continue

        yield PatentFetchResult(application_number=app, raw_record=record)

    if page_size <= 0:
        raise ValueError("page_size must be positive")

    for start in range(0, len(pending), page_size):
        batch = pending[start : start + page_size]
        logger.info("Fetching %d patent applications via /applications/search", len(batch))
        yield from _fetch_patent_batch(storage, client, batch)

    logger.info("Fetched %d patent application outcomes", len(apps))


__all__ = ["fetch_proceedings", "fetch_decisions", "fetch_petitions", "fetch_patents"]
