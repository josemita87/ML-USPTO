"""Project-wide compiled regex patterns, sourced from `config/labels.yaml`.

Separated from `schemas.constants` so the regex spec, its compile flags, and
its match contract live in one place. Constants that aren't regex stay in
`schemas.constants`.
"""

import re
from functools import lru_cache
from typing import NamedTuple

import yaml

from ml_uspto import paths


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(paths.LABELS_YAML) as f:
        return yaml.safe_load(f)


_fwd_pdf = _load()["fwd_pdf_outcome"]


class FwdOutcomePattern(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    label: int


FWD_PDF_OUTCOME_PATTERNS: tuple[FwdOutcomePattern, ...] = tuple(
    FwdOutcomePattern(
        name=p["name"],
        pattern=re.compile(p["regex"], re.IGNORECASE | re.DOTALL),
        label=int(p["label"]),
    )
    for p in _fwd_pdf["patterns"]
)


__all__ = ["FwdOutcomePattern", "FWD_PDF_OUTCOME_PATTERNS"]
