"""Parse-local enums.

Used to keep callers off of free-string Literals: `flatten(records, Parser.PROCEEDINGS)`
instead of `flatten(records, "proceedings")`.
"""

from enum import StrEnum


class Parser(StrEnum):
    """Top-level keys in `config/parsers/patents.yaml`."""

    PROCEEDINGS = "proceedings"
    DECISIONS = "decisions"
    DOCUMENTS = "documents"
    PATENTS = "patents"


class ParserTransform(StrEnum):
    """Generic transforms supported by declarative parser column specs."""

    COUNT = "count"
    UNIQUE_LIST = "unique_list"
    YN_BOOL = "yn_bool"


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
