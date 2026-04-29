"""Lock in petition-picker behavior against the empirical failure modes.

Each fixture is a real title observed in `data.uspto.gov` during the
2026-04-27 picker stress-probe (239 stratified trials). The cases cover:
  - the 7 V1 misses/false-positives that V2 fixes,
  - the 2 quarantine cases V2 correctly rejects (no petition picked),
  - multi-petition trials where the picker must take the lowest paper #,
  - exhibit cover sheets that mention "petition" (must not be picked).
"""

from ml_uspto.parse.petition_picker import pick_petition


def doc(number, title, category="Paper"):
    return {
        "documentData": {
            "documentNumber": number,
            "documentTitleText": title,
            "documentCategory": category,
        }
    }


def test_standard_post_2022_petition():
    rows = [
        doc(1, "Petitioner's Powers of Attorney"),
        doc(2, "Petitioner's Notice Ranking Petitions"),
        doc(3, "Petition for Inter Partes Review", category="PETITION"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 3


def test_legacy_paper_bucket_with_petition_in_title():
    rows = [
        doc(1, "Petitioner's Powers of Attorney"),
        doc(2, "Petition for IPR of 5478650"),
        doc(3, "Notice of filing date accorded"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 2


def test_title_omits_petition_uses_inter_partes_review_of():
    """IPR2012-00005 / IPR2025-00005: title is 'Inter Partes Review of [patent]'."""
    rows = [
        doc(1, "CAFC - Affirmed"),
        doc(2, "Inter Partes Review of 6,653,215"),
        doc(3, "Exhibit Cover Letter"),
        doc(4, "Power of Attorney"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 2


def test_title_uses_request_for_ipr():
    """IPR2013-00064: 'Request for IPR of U.S. Patent No. 7,923,311'."""
    rows = [
        doc(1, "Power of Attorney"),
        doc(2, "Request for IPR of U.S. Patent No. 7,923,311"),
        doc(3, "Notice of filing date accorded"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 2


def test_petitioners_petition_passes_blacklist():
    """IPR2021-00285 / IPR2019-00041: 'Petitioner's Petition for...' must pass.

    The blacklist's `petitioner's` clause uses a negative lookahead so it
    only kills 'Petitioner's Reply', 'Petitioner's Mandatory Notices', etc.
    """
    rows = [
        doc(1, "Petitioner's Power of Attorney"),
        doc(2, "Explanation of Multiple Petitions"),
        doc(3, "Petitioner's Petition for Inter Partes Review of US Pat No. 10,468,047"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 3


def test_corrected_petition_does_not_displace_original():
    """IPR2020-01483: when 'Petition for IPR' and 'Corrected Petition for IPR'
    both exist, take the lowest documentNumber (the original).

    Also asserts that 'Petitioner's Petition Ranking and Explanation of
    Material Differences' (a procedural multi-petition filing, NOT the
    operative petition) is rejected — the title doesn't have "Petition for"
    or "Petition:", so the tightened title regex drops it before paper-number
    comparison.
    """
    rows = [
        doc(2, "Petitioner's Petition Ranking and Explanation of Material Differences"),
        doc(3, "Petition for Inter Partes Review"),
        doc(8, "Corrected Petition for Inter Partes Review of US 10,000,000"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 3


def test_notice_of_filing_date_accorded_is_blacklisted():
    """IPR2013-00072 / IPR2019-00041: 'Notice of Filing Date Accorded to
    Petition' contains 'petition' but must NOT be picked."""
    rows = [
        doc(1, "Power of Attorney"),
        doc(5, "Notice of Filing Date Accorded to Petition"),
    ]
    picked = pick_petition(rows)
    assert picked is None  # quarantine — no real petition in this slice


def test_high_paper_numbers_excluded():
    """The corrected petition at paper 100+ must not be picked even if title matches."""
    rows = [
        doc(1, "Power of Attorney"),
        doc(150, "Petition for Inter Partes Review (refiled)"),
    ]
    assert pick_petition(rows) is None


def test_exhibits_excluded_even_if_title_says_petition():
    rows = [
        doc(1, "Petitioner's Power of Attorney"),
        doc(2, "Ex. 2017 Notice of IPR Petition", category="Exhibit"),
        doc(3, "Petition for Inter Partes Review"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 3


def test_empty_input_returns_none():
    assert pick_petition([]) is None


def test_no_match_returns_none():
    rows = [doc(1, "Power of Attorney"), doc(2, "Mandatory Notices")]
    assert pick_petition(rows) is None


def test_typo_petitioner_for_inter_partes_review_caught():
    """IPR2024-01238: real-world typo where 'Petitioner' was written instead
    of 'Petition'. The 'inter partes review of' alternative catches it."""
    rows = [
        doc(1, "Notice : Power of Attorney"),
        doc(2, "Petitioner for Inter Partes Review of U.S. Patent No. 8,830,821"),
    ]
    picked = pick_petition(rows)
    assert picked["documentData"]["documentNumber"] == 2
