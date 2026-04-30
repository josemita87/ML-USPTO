"""Patent feature engineering: PatentFileWrapper → PatentSnapshot → PatentFeatures.

`cleanse_at_t0` owns leakage discipline (drops post-T₀ events,
banned `TRIAL*` event categories, and post-T₀ assignments) so any
downstream extractor can trust the snapshot is safe. `extract_features`
is pure counting and span computation on that cleansed view.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from ml_uspto.features.schemas.constants import (
    BANNED_EVENT_CATEGORIES,
    BANNED_EVENT_PREFIXES,
    EVENT_CODE_CATEGORIES,
)
from ml_uspto.features.schemas.enums import EventCategory
from ml_uspto.parse.utils import to_date
from ml_uspto.schemas.models import (
    PatentAssignment,
    PatentEvent,
    PatentFeatures,
    PatentFileWrapper,
    PatentSnapshot,
)


def _event_category(code: str) -> EventCategory:
    if any(code.startswith(prefix) for prefix in BANNED_EVENT_PREFIXES):
        return EventCategory.TRIAL
    return EVENT_CODE_CATEGORIES.get(code, EventCategory.OTHER)


def _earliest_pre_t0_assignment_date(
    assignment: PatentAssignment, t0: date
) -> date | None:
    for d in (assignment.assignment_received_date, assignment.assignment_recorded_date):
        if d is not None and d < t0:
            return d
    return None


def cleanse_at_t0(
    wrapper: PatentFileWrapper,
    *,
    trial_number: str,
    petition_filing_date: date | datetime | str,
) -> PatentSnapshot:
    """Filter a raw `PatentFileWrapper` to the leakage-safe view as of T₀.

    `application_number` is read from the wrapper itself; callers that
    have a more authoritative value (e.g. the joiner uses the
    proceedings-side application number) should pre-set
    `wrapper.application_number` rather than passing it as a kwarg.
    """
    t0 = to_date(petition_filing_date)
    if t0 is None:
        raise ValueError("petition_filing_date must be parseable")
    if not wrapper.application_number:
        raise ValueError("wrapper.application_number is required")

    cleansed_events: list[PatentEvent] = []
    for event in wrapper.events:
        if event.event_date is None or event.event_date >= t0:
            continue
        code = (event.event_code or "").strip()
        if not code:
            continue
        if _event_category(code) in BANNED_EVENT_CATEGORIES:
            continue
        cleansed_events.append(event)

    cleansed_assignments = [
        a for a in wrapper.assignments
        if _earliest_pre_t0_assignment_date(a, t0) is not None
    ]

    return PatentSnapshot(
        trial_number=trial_number,
        application_number=wrapper.application_number,
        petition_filing_date=t0,
        cpc_classifications=list(wrapper.cpc_classifications),
        events=cleansed_events,
        assignments=cleansed_assignments,
        parent_continuity=list(wrapper.parent_continuity),
    )


def _cpc_section(classifications: list[str]) -> str | None:
    for text in classifications:
        if text and text[0].isalpha():
            return text[0].upper()
    return None


def _normalize_assignee(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return " ".join(normalized.split())


def extract_features(snapshot: PatentSnapshot) -> PatentFeatures:
    """Derive count + span features from a leakage-cleansed snapshot.

    `days_grant_to_petition` is intentionally left None here — it depends
    on the proceedings-side grant date, which the snapshot doesn't carry.
    The stage-4 join populates it.
    """
    counts = {category: 0 for category in EventCategory if category is not EventCategory.TRIAL}
    event_dates: list[date] = []
    for event in snapshot.events:
        # `cleanse_at_t0` already dropped events without code/date and
        # banned categories, so we only need to bucket what's left.
        category = _event_category((event.event_code or "").strip())
        counts[category] += 1
        if event.event_date is not None:
            event_dates.append(event.event_date)

    prosecution_span_days = None
    if len(event_dates) >= 2:
        prosecution_span_days = (max(event_dates) - min(event_dates)).days

    assignee_dates: list[date] = []
    distinct_assignees: set[str] = set()
    t0 = snapshot.petition_filing_date
    for assignment in snapshot.assignments:
        adate = _earliest_pre_t0_assignment_date(assignment, t0)
        if adate is None:
            continue
        assignee_dates.append(adate)
        for assignee in assignment.assignees:
            if assignee.assignee_name:
                normalized = _normalize_assignee(assignee.assignee_name)
                if normalized:
                    distinct_assignees.add(normalized)

    days_since_last_assignment = (
        (t0 - max(assignee_dates)).days if assignee_dates else None
    )

    return PatentFeatures(
        trial_number=snapshot.trial_number,
        application_number=snapshot.application_number,
        cpc_section=_cpc_section(snapshot.cpc_classifications),
        n_events_pre_t0=sum(counts.values()),
        prosecution_span_days=prosecution_span_days,
        n_pe_pre_t0=counts[EventCategory.PE],
        n_ex_pre_t0=counts[EventCategory.EX],
        n_aa_pre_t0=counts[EventCategory.AA],
        n_ad_pre_t0=counts[EventCategory.AD],
        n_iss_pre_t0=counts[EventCategory.ISS],
        n_maint_pre_t0=counts[EventCategory.MAINT],
        n_other_pre_t0=counts[EventCategory.OTHER],
        n_office_actions=counts[EventCategory.EX],
        n_assignments_pre_t0=len(assignee_dates),
        n_distinct_assignees_pre_t0=len(distinct_assignees),
        days_since_last_assignment=days_since_last_assignment,
        n_parent_applications=len(snapshot.parent_continuity),
    )


__all__ = ["cleanse_at_t0", "extract_features"]
