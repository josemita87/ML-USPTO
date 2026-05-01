"""Project-wide compiled regex patterns, sourced from `config/labels.yaml`."""

import re
from functools import lru_cache

import yaml

from ml_uspto import paths
from ml_uspto.schemas.models import FwdOutcomePattern


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(paths.LABELS_YAML) as f:
        return yaml.safe_load(f)


_fwd_pdf = _load()["fwd_pdf_outcome"]


FWD_PDF_OUTCOME_PATTERNS: tuple[FwdOutcomePattern, ...] = tuple(
    FwdOutcomePattern(
        name=p["name"],
        pattern=re.compile(p["regex"], re.IGNORECASE | re.DOTALL),
        label=int(p["label"]),
    )
    for p in _fwd_pdf["patterns"]
)


__all__ = ["FWD_PDF_OUTCOME_PATTERNS"]
