"""Parse-local enums."""

from enum import StrEnum


class Parser(StrEnum):
    """Top-level keys in `config/parsers/patents.yaml`."""

    PROCEEDINGS = "proceedings"
    DECISIONS = "decisions"
