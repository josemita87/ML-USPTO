"""Compiled regex patterns for `ml_uspto.parse`, sourced from
`config/petition_picker.yaml`.

Separated from `parse.schemas.constants` per the CLAUDE.md "regex separate
from non-regex" convention. Non-regex values (paper-number ceiling, exhibit
categories, sample-titles limit) stay in `parse.schemas.constants`.
"""

import re
from functools import lru_cache

import yaml

from ml_uspto import paths


@lru_cache(maxsize=1)
def _load_petition_picker() -> dict:
    with open(paths.PETITION_PICKER_YAML) as f:
        return yaml.safe_load(f)


_cfg = _load_petition_picker()

PETITION_TITLE: re.Pattern[str] = re.compile("|".join(_cfg["title_alternatives"]), re.I)
BLACKLIST: re.Pattern[str] = re.compile("|".join(_cfg["blacklist_alternatives"]), re.I)


__all__ = ["PETITION_TITLE", "BLACKLIST"]
