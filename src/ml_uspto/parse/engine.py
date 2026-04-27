"""Model-agnostic parser engine.

Reads a YAML mapping (`{output_column: dotted.path.into.record}`) and
flattens a list of nested JSON records into a flat DataFrame. New API
surfaces are added by dropping a YAML file into `config/parsers/`, not by
writing more `_flatten_*` functions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

import pandas as pd
import yaml

from ml_uspto.settings import PROJECT_ROOT

PARSERS_DIR = PROJECT_ROOT / "config" / "parsers"


@lru_cache(maxsize=None)
def load_parser_config(name: str) -> dict[str, Any]:
    """Load `config/parsers/<name>.yaml` and return the parsed dict."""
    path = PARSERS_DIR / f"{name}.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


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


def flatten(records: Iterable[Mapping[str, Any]], parser_name: str) -> pd.DataFrame:
    """Convenience: load the named parser config and flatten in one call."""
    return flatten_records(records, load_parser_config(parser_name))


__all__ = ["PARSERS_DIR", "flatten", "flatten_records", "load_parser_config"]
