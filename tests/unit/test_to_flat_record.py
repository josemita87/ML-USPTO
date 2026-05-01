"""Pin the per-app flat-record shape that lands in `patents.parquet`.

Scalars come from raw payload paths; nested bags become parallel-array
columns. Per-assignment grouping for assignees is preserved as
`list[list[str]]`.
"""

from datetime import date

from ml_uspto.parse.patents import parse_patent_wrapper, to_flat_record


def _payload() -> dict:
    return {
        "applicationNumberText": "14709428",
        "applicationMetaData": {
            "filingDate": "2015-05-11",
            "effectiveFilingDate": "2015-05-11",
            "grantDate": "2017-01-01",
            "applicationTypeCode": "Utility",
            "entityStatusData": {"businessEntityStatusCategory": "Large"},
            "firstInventorToFileIndicator": "Y",
            "nationalStageIndicator": "N",
            "inventorBag": [
                {"correspondenceAddressBag": [{"countryCode": "US"}]},
                {"correspondenceAddressBag": [{"countryCode": "CN"}]},
                {"correspondenceAddressBag": [{"countryCode": "US"}]},
            ],
            "cpcClassificationBag": ["H04W 88/06", "G06F 17/00"],
            "uspcSymbolText": "709/220",
        },
        "recordAttorney": {"attorneyBag": [{"name": "A"}, {"name": "B"}]},
        "patentTermAdjustmentData": {
            "aDelayQuantity": 12,
            "bDelayQuantity": 0,
            "cDelayQuantity": 0,
            "adjustmentTotalQuantity": 12,
            "applicantDayDelayQuantity": 3,
        },
        "eventDataBag": [
            {"eventCode": "IEXX", "eventDate": "2015-05-11"},
            {"eventCode": "CTNF", "eventDate": "2016-01-01"},
            {"eventCode": "WIDS", "eventDate": "2016-02-01"},
        ],
        "assignmentBag": [
            {
                "assignmentReceivedDate": "2018-01-01",
                "assignmentRecordedDate": "2018-01-05",
                "assigneeBag": [
                    {"assigneeNameText": "Apple Inc."},
                    {"assigneeNameText": "APPLE INC"},
                ],
            },
            {
                "assignmentReceivedDate": "2022-06-01",
                "assignmentRecordedDate": None,
                "assigneeBag": [{"assigneeNameText": "Post T0 LLC"}],
            },
        ],
        "parentContinuityBag": [
            {"parentApplicationNumberText": "11111111"},
            {"parentApplicationNumberText": "22222222"},
        ],
    }


def test_parse_patent_wrapper_typed_view():
    """Parse a raw file-wrapper payload into the typed view."""
    wrapper = parse_patent_wrapper(_payload())
    assert wrapper.application_number == "14709428"
    assert wrapper.cpc_classifications == ["H04W 88/06", "G06F 17/00"]
    assert len(wrapper.events) == 3
    assert len(wrapper.assignments) == 2
    assert len(wrapper.parent_continuity) == 2


def test_flat_record_scalars():
    """Flatten scalar patent wrapper fields into model columns."""
    flat = to_flat_record(_payload())
    assert flat["application_number"] == "14709428"
    assert flat["filing_date"] == date(2015, 5, 11)
    # `grant_date` deliberately not emitted: proceedings already carries it
    # via `patentOwnerData.grantDate`, and emitting both sides collides on
    # the joiner's left-join (pandas would suffix to `_x`/`_y`).
    assert "grant_date" not in flat
    assert flat["application_type"] == "Utility"
    assert flat["entity_size"] == "Large"
    assert flat["first_inventor_to_file"] is True
    assert flat["national_stage"] is False
    assert flat["n_inventors"] == 3
    assert flat["inventor_country_codes"] == ["CN", "US"]  # deduped, sorted
    assert flat["n_attorneys_of_record"] == 2
    assert flat["pta_total"] == 12


def test_flat_record_parallel_event_arrays():
    """Preserve event codes and dates as parallel arrays."""
    flat = to_flat_record(_payload())
    assert flat["event_codes"] == ["IEXX", "CTNF", "WIDS"]
    assert flat["event_dates"] == [
        date(2015, 5, 11),
        date(2016, 1, 1),
        date(2016, 2, 1),
    ]


def test_flat_record_assignments_preserve_grouping():
    """Preserve per-assignment assignee grouping."""
    flat = to_flat_record(_payload())
    assert flat["assignment_received_dates"] == [date(2018, 1, 1), date(2022, 6, 1)]
    assert flat["assignment_recorded_dates"] == [date(2018, 1, 5), None]
    # Per-assignment grouping preserved as list[list[str]].
    assert flat["assignees_per_assignment"] == [
        ["Apple Inc.", "APPLE INC"],
        ["Post T0 LLC"],
    ]


def test_flat_record_handles_envelope_payload():
    """Search-response envelope shape: `patentFileWrapperDataBag[0]`."""
    flat = to_flat_record({"patentFileWrapperDataBag": [_payload()]})
    assert flat["application_number"] == "14709428"
    assert flat["event_codes"] == ["IEXX", "CTNF", "WIDS"]


def test_flat_record_handles_empty_bags():
    """Emit empty arrays and null scalars for absent nested bags."""
    flat = to_flat_record({"applicationNumberText": "00000000"})
    assert flat["application_number"] == "00000000"
    assert flat["event_codes"] == []
    assert flat["assignment_received_dates"] == []
    assert flat["assignees_per_assignment"] == []
    assert flat["cpc_codes"] == []
    assert flat["parent_app_numbers"] == []
    assert flat["filing_date"] is None
    assert flat["n_inventors"] == 0
