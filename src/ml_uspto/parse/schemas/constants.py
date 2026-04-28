"""Constants for `ml_uspto.parse`, sourced from `config/petition_picker.yaml`.

Regexes are compiled once at import; the YAML stores the alternatives so a
domain expert can revise them without touching Python.
"""

import re
from functools import lru_cache

import yaml

from ml_uspto import paths


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(paths.PETITION_PICKER_YAML) as f:
        return yaml.safe_load(f)


_cfg = _load()

PETITION_TITLE: re.Pattern[str] = re.compile("|".join(_cfg["title_alternatives"]), re.I)
BLACKLIST: re.Pattern[str] = re.compile("|".join(_cfg["blacklist_alternatives"]), re.I)
PAPER_NUMBER_CEILING: int = _cfg["paper_number_ceiling"]
EXHIBIT_CATEGORIES: frozenset[str] = frozenset(_cfg["exhibit_categories"])
QUARANTINE_SAMPLE_TITLES_LIMIT: int = _cfg["quarantine"]["sample_titles_limit"]
