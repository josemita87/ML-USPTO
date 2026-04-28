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
