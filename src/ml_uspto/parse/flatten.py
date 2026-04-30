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

from ml_uspto import paths
from ml_uspto.parse.schemas.enums import Parser, ParserTransform


@lru_cache(maxsize=1)
def _load_all() -> dict[str, Any]:
    with open(paths.PARSERS_YAML) as f:
        return yaml.safe_load(f)


def load_parser_config(parser: Parser) -> dict[str, Any]:
    """Return the column-mapping dict for a single surface."""
    return _load_all()[parser.value]


def _get_path(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if part.endswith("[]"):
            key = part[:-2]
            if isinstance(obj, list):
                values: list[Any] = []
                for item in obj:
                    if not isinstance(item, Mapping):
                        continue
                    value = item.get(key)
                    if isinstance(value, list):
                        values.extend(value)
                    elif value is not None:
                        values.append(value)
                obj = values
                continue

            if not isinstance(obj, Mapping):
                return []
            obj = obj.get(key)
            if obj is None:
                return []
            if not isinstance(obj, list):
                obj = [obj]
            continue

        if isinstance(obj, list):
            values: list[Any] = []
            for item in obj:
                if not isinstance(item, Mapping):
                    continue
                value = item.get(part)
                if isinstance(value, list):
                    values.extend(value)
                elif value is not None:
                    values.append(value)
            obj = values
            continue

        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(part)
    return obj


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _unique_list(value: Any) -> list[Any]:
    out: list[Any] = []
    seen: set[Any] = set()
    for item in _as_list(value):
        if item is None:
            continue
        key = item if isinstance(item, (str, int, float, bool)) else repr(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _yn_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"y", "yes", "true", "1"}:
        return True
    if normalized in {"n", "no", "false", "0"}:
        return False
    return None


def _apply_transform(value: Any, transform: ParserTransform) -> Any:
    if transform is ParserTransform.COUNT:
        return len(_as_list(value))
    if transform is ParserTransform.UNIQUE_LIST:
        return _unique_list(value)
    if transform is ParserTransform.YN_BOOL:
        return _yn_bool(value)
    raise ValueError(f"Unsupported parser transform: {transform.value}")


def _extract_column(rec: Mapping[str, Any], spec: Any) -> Any:
    if isinstance(spec, str):
        return _get_path(rec, spec)
    if not isinstance(spec, Mapping):
        raise TypeError(
            f"Parser column spec must be a string or mapping, got {type(spec).__name__}"
        )

    value = _get_path(rec, spec["path"])
    transform = spec.get("transform")
    if transform is None:
        return value
    return _apply_transform(value, ParserTransform(transform))


def flatten_records(
    records: Iterable[Mapping[str, Any]], config: Mapping[str, Any]
) -> pd.DataFrame:
    """Apply a YAML column-mapping to each record and return a flat DataFrame."""
    columns: dict[str, Any] = config["columns"]
    rows = [{col: _extract_column(rec, spec) for col, spec in columns.items()} for rec in records]
    return pd.DataFrame(rows, columns=list(columns))


def flatten(records: Iterable[Mapping[str, Any]], parser: Parser) -> pd.DataFrame:
    """Convenience: load the parser config for `parser` and flatten in one call."""
    return flatten_records(records, load_parser_config(parser))


__all__ = ["flatten", "flatten_records", "load_parser_config"]
