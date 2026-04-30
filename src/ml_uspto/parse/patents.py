"""Raw `/applications/{appNum}` payload → typed `PatentFileWrapper`.

The parse layer's contract for the patents surface: take the JSON USPTO
returns (either the bare wrapper record or the `patentFileWrapperDataBag`
envelope from `/applications/search`) and produce a typed Pydantic
record. `features.patents.cleanse_at_t0` consumes that typed record —
the features layer never reaches into raw `Mapping[str, Any]` shapes.

Two normalizations the model can't express via aliases alone:
  - The search-response envelope `patentFileWrapperDataBag[0]` vs. the
    bare-record shape — unwrapped here.
  - `applicationMetaData.cpcClassificationBag` entries are inconsistently
    typed across the corpus (sometimes `list[str]`, sometimes
    `list[dict]`, sometimes a single non-list value). Normalized to a
    flat `list[str]` before model validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ml_uspto.schemas.models import PatentFileWrapper


def _unwrap_record(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    bag = payload.get("patentFileWrapperDataBag")
    if isinstance(bag, list) and bag and isinstance(bag[0], Mapping):
        return bag[0]
    return payload


def _normalize_cpc_classifications(record: Mapping[str, Any]) -> list[str]:
    app_meta = record.get("applicationMetaData") or {}
    if not isinstance(app_meta, Mapping):
        return []
    raw = app_meta.get("cpcClassificationBag") or []
    if not isinstance(raw, list):
        raw = [raw]
    out: list[str] = []
    for value in raw:
        if isinstance(value, Mapping):
            text = next((str(v).strip() for v in value.values() if v), "")
        else:
            text = str(value).strip()
        if text:
            out.append(text)
    return out


def parse_patent_wrapper(payload: Mapping[str, Any]) -> PatentFileWrapper:
    """Construct a typed `PatentFileWrapper` from a raw API payload."""
    record = _unwrap_record(payload)
    return PatentFileWrapper.model_validate(
        {**record, "cpc_classifications": _normalize_cpc_classifications(record)}
    )


__all__ = ["parse_patent_wrapper"]
