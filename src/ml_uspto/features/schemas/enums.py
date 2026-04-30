"""Subpackage-local enums for `ml_uspto.features`.

Used by `features.patent_aggregator` to categorize patent file-wrapper
event codes — `EventCategory` is the closed set of families the
aggregator counts (the `n_<cat>_pre_t0` columns of `PatentFeatures`).
"""

from enum import StrEnum


class EventCategory(StrEnum):
    """Patent file-wrapper event families loaded from `config/patents/event_codes.yaml`."""

    EX = "EX"
    AA = "AA"
    PE = "PE"
    AD = "AD"
    ISS = "ISS"
    MAINT = "MAINT"
    TRIAL = "TRIAL"
    OTHER = "OTHER"
