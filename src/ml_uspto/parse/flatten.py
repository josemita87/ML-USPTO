"""Model-agnostic parser engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

import pandas as pd
import yaml

from ml_uspto import paths
from ml_uspto.parse.schemas.enums import Parser


@lru_cache(maxsize=1)
def _load_all() -> dict[str, Any]:
    with open(paths.PARSERS_YAML) as f:
        return yaml.safe_load(f)


def load_parser_config(parser: Parser) -> dict[str, Any]:
    """Return the column-mapping dict for a single API surface from YAML."""
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
    """Apply a YAML column-mapping (`{output_column: dotted.path}`) to each record.

    Args:
        records: Iterable of nested JSON records.
        config: Parser config dict with a `columns` key mapping output
            column names to dotted paths into the record.

    Returns:
        Flat DataFrame in the column order declared by `config`.
    """
    columns: dict[str, str] = config["columns"]
    rows = [{col: _get_path(rec, path) for col, path in columns.items()} for rec in records]
    return pd.DataFrame(rows, columns=list(columns))


def flatten(records: Iterable[Mapping[str, Any]], parser: Parser) -> pd.DataFrame:
    """Load the parser config for `parser` and flatten `records` in one call."""
    return flatten_records(records, load_parser_config(parser))


__all__ = ["flatten", "flatten_records", "load_parser_config"]
