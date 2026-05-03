"""Ingestion fetchers — paginate/search, cache, flatten."""

from __future__ import annotations

import io
import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import pandas as pd
import pdfplumber
import requests

from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.schemas.constants import PETITION_SCAN_CATEGORIES, STAGE_RECORDS_KEY
from ml_uspto.ingest.schemas.enums import FwdPdfCandidateColumn, Stage
from ml_uspto.parse.flatten import flatten
from ml_uspto.parse.schemas.enums import Parser
from ml_uspto.protocols.storage import Storage
from ml_uspto.schemas.enums import Frame
from ml_uspto.schemas.models import (
    DecisionPdfFetchResult,
    PatentFetchResult,
    PetitionPdfFetchResult,
)

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
    here. The full payload is cached (not just the records) so the
    `count` field survives resumes. The records-bag key is looked up
    from `bucket` via `STAGE_RECORDS_KEY` — callers don't pass it.

    Args:
        storage: Backend for the on-disk page cache.
        client_method: Bound `USPTOClient.search_*_post` method.
        filters: Forwarded to `client_method` verbatim.
        bucket: Stage whose pages are being fetched (also the cache
            bucket).
        page_size: Records per page.
        max_pages: Optional cap for smoke runs. The cap stops the loop
            *before* fetching, so already-cached pages past the cap
            are not consumed even though they exist on disk.

    Yields:
        Records from successive pages.
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
    `config/parsers/patents.yaml`.

    Args:
        storage: Page-cache + processed-frame backend.
        client: USPTO ODP client.
        page_size: Records per page (typically
            `get_settings().api.page_size` — no in-function default).
        max_pages: Optional cap for smoke runs.

    Returns:
        The flattened proceedings frame, also persisted as
        `Frame.TRIALS`.
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

    Required to derive the `cancelled` label for trials with `Final
    Written Decision` status — `parse.labels` looks up the terminating
    FWD's `trialOutcomeCategory` per trial.

    Args:
        storage: Page-cache + processed-frame backend.
        client: USPTO ODP client.
        page_size: Records per page.
        max_pages: Optional cap for smoke runs.

    Returns:
        The flattened decisions frame, persisted as `Frame.DECISIONS`.
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
    pre-2022 petitions live in the `Paper` catch-all.

    The iterator is single-pass (one page resident at a time); pass it
    directly to `parse.petitions.assemble_petitions` to avoid
    materializing all ~323K records.

    Args:
        storage: Page-cache backend.
        client: USPTO ODP client.
        page_size: Records per page.
        max_pages: Optional cap for smoke runs.

    Yields:
        Raw document records.
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
        # 413 Request-Entity-Too-Large is empirically driven by *response*
        # payload size for /applications/search — a few apps in any random
        # batch have file wrappers large enough that a 80-app batch
        # exceeds the API gateway's response cap. Recursive bisect isolates
        # them; a single-app batch that still 413s is a hard failure.
        status = exc.response.status_code if exc.response is not None else None
        if status == 413 and len(application_numbers) > 1:
            mid = len(application_numbers) // 2
            yield from _fetch_patent_batch(storage, client, application_numbers[:mid])
            yield from _fetch_patent_batch(storage, client, application_numbers[mid:])
            return
        logger.warning(
            "Patent batch HTTP error (status=%s, %d apps): %s",
            status, len(application_numbers), exc,
        )
        for app in application_numbers:
            yield PatentFetchResult(application_number=app)
        return
    except requests.RequestException as exc:
        logger.warning(
            "Patent batch request error (%d apps): %s",
            len(application_numbers), exc,
        )
        for app in application_numbers:
            yield PatentFetchResult(application_number=app)
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
            logger.info("Patent application %s not returned by /applications/search", app)
            yield PatentFetchResult(application_number=app)
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

    Uncached applications are fetched in batches through
    `/applications/search` filtered by `applicationNumberText`.
    Successes cache one raw wrapper payload under bucket
    `Stage.PATENTS`, keyed by `application_number`. Missing applications
    and request failures cache explicit fetch-error payloads so reruns
    do not repeatedly hit known-missing apps.

    Args:
        storage: Page-cache backend.
        client: USPTO ODP client.
        application_numbers: Application numbers to fetch (deduped
            internally).
        page_size: Batch size for `/applications/search`.
        max_apps: Optional cap on the number of distinct applications.

    Yields:
        One `PatentFetchResult` per requested application.

    Raises:
        ValueError: If `page_size` is not positive.
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
        record = _extract_patent_record(payload, app)
        if record is None:
            # Cached payload exists but is shapeless (e.g. legacy `_fetch_error`
            # stub from before quarantines were removed). Treat as a miss and
            # let the next fetch attempt overwrite it.
            pending.append(app)
            continue

        yield PatentFetchResult(application_number=app, raw_record=record)

    if page_size <= 0:
        raise ValueError("page_size must be positive")

    for start in range(0, len(pending), page_size):
        batch = pending[start : start + page_size]
        logger.info("Fetching %d patent applications via /applications/search", len(batch))
        yield from _fetch_patent_batch(storage, client, batch)

    logger.info("Fetched %d patent application outcomes", len(apps))


def fetch_decision_pdfs(
    storage: Storage,
    client: USPTOClient,
    candidates: pd.DataFrame,
) -> Iterator[DecisionPdfFetchResult]:
    """Download each candidate FWD PDF, extract text, cache, yield outcomes.

    Successful downloads run pdfplumber over the PDF bytes and land the
    full opinion text at `Stage.DECISION_TEXTS / <doc_id>.txt`; the
    binary is never persisted. Failures are logged and yield a result
    with `bytes_written=None`; the next cron's gap-detector will pick
    the same doc up again.

    Pacing relies on pdfplumber's 5–25s/file CPU cost (which keeps the
    request rate ~20× under the 1.2M/wk PDF bucket) plus the 429 backoff
    inside `USPTOClient.download_pdf`.

    Args:
        storage: Backend for cached opinion-text blobs.
        client: USPTO ODP client.
        candidates: Frame returned by
            `ingest.decisions.enumerate_missing_fwd_pdfs` — already
            filtered for cache hits, so every row is a fresh attempt.

    Yields:
        One `DecisionPdfFetchResult` per row.
    """
    doc_col = FwdPdfCandidateColumn.DOCUMENT_IDENTIFIER.value
    uri_col = FwdPdfCandidateColumn.FILE_DOWNLOAD_URI.value

    for _, row in candidates.iterrows():
        doc_id = str(row[doc_col])
        uri = str(row[uri_col])
        try:
            payload = client.download_pdf(uri)
        except requests.HTTPError as exc:
            # Errors are already slow + the 429 backoff inside
            # download_pdf already paced this attempt — don't sleep again.
            response = exc.response
            status = response.status_code if response is not None else None
            logger.warning("FWD-PDF download failed (HTTP %s) doc=%s: %s", status, doc_id, exc)
            yield DecisionPdfFetchResult(document_identifier=doc_id)
            continue
        except (requests.RequestException, RuntimeError) as exc:
            logger.warning("FWD-PDF request error doc=%s: %s", doc_id, exc)
            yield DecisionPdfFetchResult(document_identifier=doc_id)
            continue

        if not payload:
            logger.warning("FWD-PDF empty response doc=%s", doc_id)
            yield DecisionPdfFetchResult(document_identifier=doc_id)
            continue

        try:
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as exc:  # noqa: BLE001 — pdfplumber raises a zoo of types
            logger.warning(
                "FWD-PDF pdfplumber error doc=%s: %s: %s",
                doc_id,
                type(exc).__name__,
                exc,
            )
            yield DecisionPdfFetchResult(document_identifier=doc_id)
            continue

        text_bytes = text.encode("utf-8")
        storage.save_blob(Stage.DECISION_TEXTS, doc_id, "txt", text_bytes)
        yield DecisionPdfFetchResult(document_identifier=doc_id, bytes_written=len(text_bytes))


def fetch_petition_pdfs(
    storage: Storage,
    client: USPTOClient,
    candidates: pd.DataFrame,
) -> Iterator[PetitionPdfFetchResult]:
    """Download each candidate petition PDF, extract text, cache, yield.

    Mirrors `fetch_decision_pdfs`: the extracted text lands at
    `Stage.PETITION_TEXTS / <trial_number>.txt` (text-only — the binary
    is never persisted). Failures yield a result with `bytes_written=None`;
    the next cron's gap-detector picks them up again.

    Pacing: pdfplumber over a 60–120-page petition is the natural pacer;
    bursts are absorbed by the 429 backoff inside
    `USPTOClient.download_pdf`.

    Args:
        storage: Backend for cached petition-text blobs.
        client: USPTO ODP client.
        candidates: Frame returned by
            `ingest.petitions.enumerate_missing_petition_pdfs` —
            already filtered for cache hits and missing-URI rows.

    Yields:
        One `PetitionPdfFetchResult` per row.
    """
    trials = candidates["trial_number"].astype(str)
    uris = candidates["petition_pdf_uri"].astype(str)
    for trial, uri in zip(trials, uris, strict=True):
        try:
            payload = client.download_pdf(uri)
        except requests.HTTPError as exc:
            response = exc.response
            status = response.status_code if response is not None else None
            logger.warning(
                "Petition-PDF download failed (HTTP %s) trial=%s: %s", status, trial, exc
            )
            yield PetitionPdfFetchResult(trial_number=trial)
            continue
        except (requests.RequestException, RuntimeError) as exc:
            logger.warning("Petition-PDF request error trial=%s: %s", trial, exc)
            yield PetitionPdfFetchResult(trial_number=trial)
            continue

        if not payload:
            logger.warning("Petition-PDF empty response trial=%s", trial)
            yield PetitionPdfFetchResult(trial_number=trial)
            continue

        try:
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as exc:  # noqa: BLE001 — pdfplumber raises a zoo of types
            logger.warning(
                "Petition-PDF pdfplumber error trial=%s: %s: %s",
                trial,
                type(exc).__name__,
                exc,
            )
            yield PetitionPdfFetchResult(trial_number=trial)
            continue

        text_bytes = text.encode("utf-8")
        storage.save_blob(Stage.PETITION_TEXTS, trial, "txt", text_bytes)
        yield PetitionPdfFetchResult(
            trial_number=trial,
            bytes_written=len(text_bytes),
        )


__all__ = [
    "fetch_proceedings",
    "fetch_decisions",
    "fetch_petitions",
    "fetch_patents",
    "fetch_decision_pdfs",
    "fetch_petition_pdfs",
]
