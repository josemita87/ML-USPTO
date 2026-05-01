"""Petition-picker numeric/categorical constants for `ml_uspto.parse`."""

from functools import lru_cache

import yaml

from ml_uspto import paths
from ml_uspto.schemas.enums import DocumentCategory


@lru_cache(maxsize=1)
def _load_petition_picker() -> dict:
    with open(paths.PETITION_PICKER_YAML) as f:
        return yaml.safe_load(f)


_cfg = _load_petition_picker()

PAPER_NUMBER_CEILING: int = _cfg["paper_number_ceiling"]
EXHIBIT_CATEGORIES: frozenset[DocumentCategory] = frozenset(
    DocumentCategory(s) for s in _cfg["exhibit_categories"]
)
