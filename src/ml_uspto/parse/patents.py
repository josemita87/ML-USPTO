"""Raw `/applications/{appNum}` payload → flat per-app record."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ml_uspto.utils import to_date
from ml_uspto.schemas.models import PatentFileWrapper


def _unwrap_record(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    bag = payload.get("patentFileWrapperDataBag")
    if isinstance(bag, list) and bag and isinstance(bag[0], Mapping):
        return bag[0]
    return payload


def _normalize_cpc_classifications(record: Mapping[str, Any]) -> list[str]:
    """Normalize `cpcClassificationBag` entries to a flat `list[str]`.

    Entries are inconsistently typed across the corpus: sometimes
    `list[str]`, sometimes `list[dict]`, sometimes a single non-list
    value. This function flattens all variants.

    Args:
        record: Unwrapped patent file-wrapper record.

    Returns:
        Flat list of CPC code strings.
    """
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
    """Construct a typed `PatentFileWrapper` from a raw API payload.

    Accepts either the bare wrapper record or the
    `patentFileWrapperDataBag` envelope. Used as the per-app
    validation contract; flatten via `to_flat_record` for the parquet
    row.
    """
    record = _unwrap_record(payload)
    return PatentFileWrapper.model_validate(
        {**record, "cpc_classifications": _normalize_cpc_classifications(record)}
    )


def _yn_bool(value: Any) -> bool | None:
    if value is None:
        return None
    s = str(value).strip().upper()
    if s == "Y":
        return True
    if s == "N":
        return False
    return None


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _unique_country_codes(inventor_bag: Any) -> list[str]:
    if not isinstance(inventor_bag, list):
        return []
    seen: set[str] = set()
    for inventor in inventor_bag:
        if not isinstance(inventor, Mapping):
            continue
        for address in inventor.get("correspondenceAddressBag") or []:
            if not isinstance(address, Mapping):
                continue
            code = address.get("countryCode")
            if isinstance(code, str) and code:
                seen.add(code)
    return sorted(seen)


def to_flat_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Raw API payload → flat dict ready for `patents.parquet`.

    Scalars are read directly from the unwrapped record. Nested bags
    are extracted via the typed wrapper as parallel-array columns:
    `event_codes`/`event_dates` aligned by index,
    `assignment_received_dates`/`assignment_recorded_dates` likewise,
    and `assignees_per_assignment` as `list[list[str]]` to preserve
    which assignees belong to which assignment.

    Args:
        payload: Raw `/applications/{appNum}` response (bare record
            or `patentFileWrapperDataBag` envelope).

    Returns:
        Flat dict, one row per app. `grant_date` is intentionally
        absent — proceedings already carries it via
        `patentOwnerData.grantDate`, and including it here would
        collide on the joiner's left-join.
    """
    record = _unwrap_record(payload)
    wrapper = PatentFileWrapper.model_validate(
        {**record, "cpc_classifications": _normalize_cpc_classifications(record)}
    )

    app_meta = record.get("applicationMetaData") or {}
    record_attorney = record.get("recordAttorney") or {}
    pta = record.get("patentTermAdjustmentData") or {}
    entity_status = app_meta.get("entityStatusData") or {}

    flat: dict[str, Any] = {
        "application_number": wrapper.application_number,
        "filing_date": to_date(app_meta.get("filingDate")),
        "effective_filing_date": to_date(app_meta.get("effectiveFilingDate")),
        "application_type": app_meta.get("applicationTypeCode"),
        "entity_size": entity_status.get("businessEntityStatusCategory"),
        "first_inventor_to_file": _yn_bool(app_meta.get("firstInventorToFileIndicator")),
        "national_stage": _yn_bool(app_meta.get("nationalStageIndicator")),
        "n_inventors": _count(app_meta.get("inventorBag")),
        "inventor_country_codes": _unique_country_codes(app_meta.get("inventorBag")),
        "uspc_class_subclass": app_meta.get("uspcSymbolText"),
        "n_attorneys_of_record": _count(record_attorney.get("attorneyBag")),
        # Patent term adjustment — frozen at grant.
        "pta_a_delay": pta.get("aDelayQuantity"),
        "pta_b_delay": pta.get("bDelayQuantity"),
        "pta_c_delay": pta.get("cDelayQuantity"),
        "pta_total": pta.get("adjustmentTotalQuantity"),
        "pta_applicant_delay": pta.get("applicantDayDelayQuantity"),
        # Parallel-array columns from the typed wrapper. Per-assignment
        # grouping for assignees is preserved as list-of-list.
        "cpc_codes": list(wrapper.cpc_classifications),
        "event_codes": [e.event_code for e in wrapper.events],
        "event_dates": [e.event_date for e in wrapper.events],
        "assignment_received_dates": [
            a.assignment_received_date for a in wrapper.assignments
        ],
        "assignment_recorded_dates": [
            a.assignment_recorded_date for a in wrapper.assignments
        ],
        "assignees_per_assignment": [
            [a.assignee_name for a in asgmt.assignees if a.assignee_name]
            for asgmt in wrapper.assignments
        ],
        "parent_app_numbers": [
            p.application_number for p in wrapper.parent_continuity if p.application_number
        ],
    }
    return flat


__all__ = ["parse_patent_wrapper", "to_flat_record"]
