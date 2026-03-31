"""USPTO Open Data Portal API client for PTAB data."""

import logging
import time

import requests

from src.settings import get_settings

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

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Failed after 3 retries: {url}")
