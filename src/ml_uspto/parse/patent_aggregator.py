"""T0-safe aggregation for patent application file-wrapper bags.

`eventDataBag` and `assignmentBag` are dated, mixed pre/post-petition bags.
This module is the single place that filters them relative to T0
(`petition_filing_date`) before producing fixed-width `PatentFeatures`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from ml_uspto.parse.dates import to_date
from ml_uspto.parse.schemas.constants import (
    BANNED_EVENT_CATEGORIES,
    BANNED_EVENT_PREFIXES,
    EVENT_CODE_CATEGORIES,
)
from ml_uspto.parse.schemas.enums import EventCategory
from ml_uspto.schemas.models import PatentFeatures


def _wrapper_record(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    bag = payload.get("patentFileWrapperDataBag")
    if isinstance(bag, list) and bag and isinstance(bag[0], Mapping):
        return bag[0]
    return payload


def _dated_before_t0(row: Mapping[str, Any], field: str, t0: date) -> date | None:
    row_date = to_date(row.get(field))
    if row_date is None or row_date >= t0:
        return None
    return row_date


def _event_category(code: str) -> EventCategory:
    if any(code.startswith(prefix) for prefix in BANNED_EVENT_PREFIXES):
        return EventCategory.TRIAL
    return EVENT_CODE_CATEGORIES.get(code, EventCategory.OTHER)


def _event_counts(
    events: list[Mapping[str, Any]], t0: date
) -> tuple[dict[EventCategory, int], list[date]]:
    counts = {category: 0 for category in EventCategory if category is not EventCategory.TRIAL}
    dates: list[date] = []
    for event in events:
        event_date = _dated_before_t0(event, "eventDate", t0)
        if event_date is None:
            continue
        code = str(event.get("eventCode") or "").strip()
        if not code:
            continue
        category = _event_category(code)
        if category in BANNED_EVENT_CATEGORIES:
            continue
        counts[category] += 1
        dates.append(event_date)
    return counts, dates


def _cpc_section(record: Mapping[str, Any]) -> str | None:
    app_meta = record.get("applicationMetaData") or {}
    if not isinstance(app_meta, Mapping):
        return None
    cpc_values = app_meta.get("cpcClassificationBag") or []
    if not isinstance(cpc_values, list):
        cpc_values = [cpc_values]
    for value in cpc_values:
        if isinstance(value, Mapping):
            text = next((str(v).strip() for v in value.values() if v), "")
        else:
            text = str(value).strip()
        if text and text[0].isalpha():
            return text[0].upper()
    return None


def _assignee_names(assignment: Mapping[str, Any]) -> list[str]:
    assignees = assignment.get("assigneeBag") or []
    if not isinstance(assignees, list):
        assignees = [assignees]
    names: list[str] = []
    for assignee in assignees:
        if not isinstance(assignee, Mapping):
            continue
        name = assignee.get("assigneeNameText")
        if name:
            names.append(str(name))
    return names


def _normalize_assignee(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return " ".join(normalized.split())


def _assignment_date(assignment: Mapping[str, Any], t0: date) -> date | None:
    for field in ("assignmentReceivedDate", "assignmentRecordedDate"):
        assignment_date = _dated_before_t0(assignment, field, t0)
        if assignment_date is not None:
            return assignment_date
    return None


def _assignment_features(
    assignments: list[Mapping[str, Any]], t0: date
) -> tuple[int, int, int | None]:
    dates: list[date] = []
    assignees: set[str] = set()
    for assignment in assignments:
        assignment_date = _assignment_date(assignment, t0)
        if assignment_date is None:
            continue
        dates.append(assignment_date)
        for name in _assignee_names(assignment):
            normalized = _normalize_assignee(name)
            if normalized:
                assignees.add(normalized)

    days_since_last = (t0 - max(dates)).days if dates else None
    return len(dates), len(assignees), days_since_last


def _as_mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def aggregate_patent(
    payload: Mapping[str, Any],
    *,
    trial_number: str,
    application_number: str,
    petition_filing_date: date | datetime | str,
) -> PatentFeatures:
    """Aggregate dated file-wrapper bags into T0-safe patent features.

    `days_grant_to_petition` is intentionally left for the stage-4 join because
    it depends on the proceedings-side grant date, not the file-wrapper record.
    """
    t0 = to_date(petition_filing_date)
    if t0 is None:
        raise ValueError("petition_filing_date must be parseable")

    record = _wrapper_record(payload)
    events = _as_mapping_list(record.get("eventDataBag"))
    event_counts, event_dates = _event_counts(events, t0)
    n_events_pre_t0 = sum(event_counts.values())

    prosecution_span_days = None
    if len(event_dates) >= 2:
        prosecution_span_days = (max(event_dates) - min(event_dates)).days

    assignments = _as_mapping_list(record.get("assignmentBag"))
    n_assignments, n_distinct_assignees, days_since_last_assignment = _assignment_features(
        assignments, t0
    )

    parent_continuity = _as_mapping_list(record.get("parentContinuityBag"))

    return PatentFeatures(
        trial_number=trial_number,
        application_number=application_number,
        cpc_section=_cpc_section(record),
        n_events_pre_t0=n_events_pre_t0,
        prosecution_span_days=prosecution_span_days,
        n_pe_pre_t0=event_counts[EventCategory.PE],
        n_ex_pre_t0=event_counts[EventCategory.EX],
        n_aa_pre_t0=event_counts[EventCategory.AA],
        n_ad_pre_t0=event_counts[EventCategory.AD],
        n_iss_pre_t0=event_counts[EventCategory.ISS],
        n_maint_pre_t0=event_counts[EventCategory.MAINT],
        n_other_pre_t0=event_counts[EventCategory.OTHER],
        n_office_actions=event_counts[EventCategory.EX],
        n_assignments_pre_t0=n_assignments,
        n_distinct_assignees_pre_t0=n_distinct_assignees,
        days_since_last_assignment=days_since_last_assignment,
        n_parent_applications=len(parent_continuity),
    )


__all__ = ["aggregate_patent"]
