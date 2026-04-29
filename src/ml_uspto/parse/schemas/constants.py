"""Constants for `ml_uspto.parse`, sourced from YAML config.

Patent event-code taxonomy and petition-picker numeric/categorical knobs.
Compiled regex patterns live in `parse.schemas.patterns`, not here.
"""

from functools import lru_cache

import yaml

from ml_uspto import paths
from ml_uspto.parse.schemas.enums import EventCategory
from ml_uspto.schemas.enums import DocumentCategory


@lru_cache(maxsize=1)
def _load_petition_picker() -> dict:
    with open(paths.PETITION_PICKER_YAML) as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def _load_patent_event_codes() -> dict:
    with open(paths.PATENT_EVENT_CODES_YAML) as f:
        return yaml.safe_load(f)


_cfg = _load_petition_picker()
_event_cfg = _load_patent_event_codes()

PAPER_NUMBER_CEILING: int = _cfg["paper_number_ceiling"]
EXHIBIT_CATEGORIES: frozenset[DocumentCategory] = frozenset(
    DocumentCategory(s) for s in _cfg["exhibit_categories"]
)
QUARANTINE_SAMPLE_TITLES_LIMIT: int = _cfg["quarantine"]["sample_titles_limit"]

EVENT_CODE_CATEGORIES: dict[str, EventCategory] = {
    code: EventCategory(category) for code, category in _event_cfg["codes"].items()
}
BANNED_EVENT_CATEGORIES: frozenset[EventCategory] = frozenset(
    EventCategory(category) for category in _event_cfg["banned_categories"]
)
BANNED_EVENT_PREFIXES: tuple[str, ...] = tuple(_event_cfg.get("banned_prefixes", []))
