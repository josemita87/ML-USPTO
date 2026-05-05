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


NON_TRIAL_CATEGORIES: tuple[EventCategory, ...] = tuple(
    cat for cat in EventCategory if cat is not EventCategory.TRIAL
)


class InventorGeo(StrEnum):
    """3-way bucket over `inventor_country_codes` (NaN survives as its own OHE level)."""

    US_ONLY = "us_only"
    ANY_FOREIGN = "any_foreign"
