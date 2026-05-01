"""Subpackage-local enums for `ml_uspto.features`."""

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
