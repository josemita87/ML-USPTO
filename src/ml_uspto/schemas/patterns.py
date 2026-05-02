"""Project-wide compiled regex patterns, sourced from `config/labels.yaml`."""

import re
from functools import lru_cache

import yaml

from ml_uspto import paths
from ml_uspto.schemas.constants import LEGACY_FWD_AMENDMENT_TITLE_MARKERS
from ml_uspto.schemas.models import FwdOutcomePattern


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(paths.LABELS_YAML) as f:
        return yaml.safe_load(f)


_fwd_pdf = _load()["fwd_pdf_outcome"]
_fwd_pdf_legacy = _load()["fwd_pdf_outcome_legacy"]


FWD_PDF_OUTCOME_PATTERNS: tuple[FwdOutcomePattern, ...] = tuple(
    FwdOutcomePattern(
        name=p["name"],
        pattern=re.compile(p["regex"], re.IGNORECASE | re.DOTALL),
        label=int(p["label"]),
    )
    for p in _fwd_pdf["patterns"]
)

LEGACY_FWD_ORDER_PATTERNS: tuple[FwdOutcomePattern, ...] = tuple(
    FwdOutcomePattern(
        name=p["name"],
        pattern=re.compile(p["regex"], re.IGNORECASE | re.DOTALL),
        label=int(p["label"]),
    )
    for p in _fwd_pdf_legacy["patterns"]
)

LEGACY_FWD_AMENDMENT_PATTERN: re.Pattern[str] = re.compile(
    "|".join(re.escape(m) for m in LEGACY_FWD_AMENDMENT_TITLE_MARKERS),
    re.IGNORECASE,
)


__all__ = [
    "FWD_PDF_OUTCOME_PATTERNS",
    "LEGACY_FWD_ORDER_PATTERNS",
    "LEGACY_FWD_AMENDMENT_PATTERN",
]
