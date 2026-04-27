"""USPTO Open Data Portal API client for PTAB data."""

import logging
import time
from typing import Any

import requests

from ml_uspto.settings import get_settings

logger = logging.getLogger(__name__)


class USPTOClient:
    def __init__(self):
        settings = get_settings()
        self.base_url = settings.api.base_url
        self.session = requests.Session()
        if settings.api.api_key:
            self.session.headers["X-API-Key"] = settings.api.api_key

    def search_proceedings(
        self, query: str = "IPR", offset: int = 0, limit: int = 100
    ) -> dict:
        """Search proceedings via GET. Query is full-text only."""
        url = f"{self.base_url}/trials/proceedings/search"
        params = {"query": query, "offset": offset, "limit": limit}
        return self._get(url, params)

    def search_decisions(
        self, query: str = "IPR", offset: int = 0, limit: int = 100
    ) -> dict:
        url = f"{self.base_url}/trials/decisions/search"
        params = {"query": query, "offset": offset, "limit": limit}
        return self._get(url, params)

    def get_proceeding(self, trial_number: str) -> dict:
        url = f"{self.base_url}/trials/proceedings/{trial_number}"
        return self._get(url)

    def get_documents(self, trial_number: str) -> dict:
        url = f"{self.base_url}/trials/{trial_number}/documents"
        return self._get(url)

    def search_proceedings_post(
        self,
        *,
        q: str | None = None,
        filters: list[dict] | None = None,
        range_filters: list[dict] | None = None,
        fields: list[str] | None = None,
        facets: list[str] | None = None,
        sort: list[dict] | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict:
        """Search proceedings via POST with ODP Simplified Query Syntax.

        Field names use dotted paths (e.g. `trialMetaData.trialTypeCode`).
        `filters` entries: {"name": "<field>", "value": [<val>, ...]}.
        `range_filters` entries: {"field": "<field>", "valueFrom": "...", "valueTo": "..."}.
        """
        url = f"{self.base_url}/trials/proceedings/search"
        body = self._build_body(q, filters, range_filters, fields, facets, sort, offset, limit)
        return self._post(url, body)

    def search_decisions_post(
        self,
        *,
        q: str | None = None,
        filters: list[dict] | None = None,
        range_filters: list[dict] | None = None,
        fields: list[str] | None = None,
        facets: list[str] | None = None,
        sort: list[dict] | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict:
        """Search decisions via POST. Supports `documentOCRText`, `statuteAndRuleBag`,
        `issueTypeBag`, etc. when requested via `fields`."""
        url = f"{self.base_url}/trials/decisions/search"
        body = self._build_body(q, filters, range_filters, fields, facets, sort, offset, limit)
        return self._post(url, body)

    def download_decisions(
        self,
        *,
        q: str | None = None,
        filters: list[dict] | None = None,
        range_filters: list[dict] | None = None,
        fields: list[str] | None = None,
        sort: list[dict] | None = None,
        offset: int = 0,
        limit: int = 25,
        format: str = "json",
    ) -> bytes:
        """Export decision search results as CSV or JSON.

        The download endpoint supports a restricted field projection —
        rich fields like `documentData.documentOCRText` are rejected.
        For OCR text and full decision metadata use `search_decisions_post`.
        """
        url = f"{self.base_url}/trials/decisions/search/download"
        body = self._build_body(q, filters, range_filters, fields, None, sort, offset, limit)
        body["format"] = format
        resp = self.session.post(url, json=body, timeout=120)
        resp.raise_for_status()
        return resp.content

    @staticmethod
    def _build_body(
        q: str | None,
        filters: list[dict] | None,
        range_filters: list[dict] | None,
        fields: list[str] | None,
        facets: list[str] | None,
        sort: list[dict] | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"pagination": {"offset": offset, "limit": limit}}
        if q is not None:
            body["q"] = q
        if filters:
            body["filters"] = filters
        if range_filters:
            body["rangeFilters"] = range_filters
        if fields:
            body["fields"] = fields
        if facets:
            body["facets"] = facets
        if sort:
            body["sort"] = sort
        return body

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                # ODP requires min 5s delay on 429; back off 5s, 10s, 20s.
                wait = 5 * (2 ** attempt)
                logger.warning("Rate limited (429), waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Failed after 3 retries: {url}")

    def _post(self, url: str, body: dict) -> dict:
        for attempt in range(3):
            resp = self.session.post(url, json=body, timeout=60)
            if resp.status_code == 429:
                # ODP requires min 5s delay on 429; back off 5s, 10s, 20s.
                wait = 5 * (2 ** attempt)
                logger.warning("Rate limited (429), waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Failed after 3 retries: {url}")
