"""Model-agnostic parser engine.

Reads a YAML mapping (`{output_column: dotted.path.into.record}`) and
flattens a list of nested JSON records into a flat DataFrame. New API
surfaces are added by appending a key to `config/parsers/patents.yaml`
and a member to `Parser`, not by writing more `_flatten_*` functions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

import pandas as pd
import yaml

from ml_uspto.parse.schemas.enums import Parser
from ml_uspto.settings import PROJECT_ROOT

PARSERS_CONFIG_PATH = PROJECT_ROOT / "config" / "parsers" / "patents.yaml"


@lru_cache(maxsize=1)
def _load_all() -> dict[str, Any]:
    with open(PARSERS_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_parser_config(parser: Parser) -> dict[str, Any]:
    """Return the column-mapping dict for a single surface."""
    return _load_all()[parser.value]


def _get_path(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(part)
    return obj


def flatten_records(
    records: Iterable[Mapping[str, Any]], config: Mapping[str, Any]
) -> pd.DataFrame:
    """Apply a YAML column-mapping to each record and return a flat DataFrame."""
    columns: dict[str, str] = config["columns"]
    rows = [{col: _get_path(rec, path) for col, path in columns.items()} for rec in records]
    return pd.DataFrame(rows, columns=list(columns))


def flatten(records: Iterable[Mapping[str, Any]], parser: Parser) -> pd.DataFrame:
    """Convenience: load the parser config for `parser` and flatten in one call."""
    return flatten_records(records, load_parser_config(parser))


__all__ = ["PARSERS_CONFIG_PATH", "flatten", "flatten_records", "load_parser_config"]
