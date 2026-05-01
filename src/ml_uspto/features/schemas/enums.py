"""Subpackage-local enums for `ml_uspto.features`.

`EventCategory` is the closed set of patent file-wrapper event families
that `features.transforms.build_features` aggregates into per-trial count
columns (`n_<cat>_pre_t0`).
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
