"""Constants for `ml_uspto.parse`, sourced from `config/petition_picker.yaml`.

Regexes are compiled once at import; the YAML stores the alternatives so a
domain expert can revise them without touching Python.
"""

import re
from functools import lru_cache

import yaml

from ml_uspto.settings import PROJECT_ROOT

_PETITION_PICKER_PATH = PROJECT_ROOT / "config" / "petition_picker.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(_PETITION_PICKER_PATH) as f:
        return yaml.safe_load(f)


_cfg = _load()

PETITION_TITLE: re.Pattern[str] = re.compile("|".join(_cfg["title_alternatives"]), re.I)
BLACKLIST: re.Pattern[str] = re.compile("|".join(_cfg["blacklist_alternatives"]), re.I)
PAPER_NUMBER_CEILING: int = _cfg["paper_number_ceiling"]
EXHIBIT_CATEGORIES: frozenset[str] = frozenset(_cfg["exhibit_categories"])
