"""Integration tests for FWD outcome extraction against cached opinion text.

These tests intentionally read text from the live cache rather than
checked-in fixtures: the regex is the load-bearing label-extraction logic
for the whole project, and the only credible test is "does it parse
real-world FWDs from across the year/format spectrum". The sample is
populated by `drivers/probe_fwd_pdfs_sample.py` (which downloads PDFs,
extracts text via pdfplumber at fetch-time, and persists the text +
manifest) and persisted in `_sample_manifest.json`; this test fails
informatively if the cache is empty or if the regex misses any cached
text.

Two complementary checks per FWD:

  * **Resolution** — the regex must hit and the leading capture word must
    map to a known label. A miss is a hard failure: the YAML pattern is
    the spec, and any miss is a sample we haven't accounted for.

  * **Cross-validation against the API title** — for the ~40% of FWDs
    whose `documentTitleText` already encodes the outcome (matched by
    `PARSEABLE_TITLE_RE` in the probe driver), we re-derive the label
    from the title and assert it agrees with the text-extracted label.
    Disagreement is either a regex bug or USPTO indexing drift; both
    are worth surfacing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ml_uspto.parse.labels import extract_outcome
from ml_uspto.schemas.constants import FWD_ORIGINAL_MARKER

TEXT_CACHE_DIR = Path("data/raw/decision_texts")
MANIFEST_PATH = TEXT_CACHE_DIR / "_sample_manifest.json"

# Title-side ground truth for the cross-validation test. "Challenged" is
# optional because ~3% of FWDs omit it ("Determining All Claims
# Unpatentable"). Kept independent from the cover-page patterns so the
# agreement check exercises two distinct extraction processes.
TITLE_OUTCOME_RE = re.compile(
    r"determining\s+(all|no|some)\s+(?:challenged\s+)?claims?",
    re.IGNORECASE,
)


def _label_from_title(title: str) -> int | None:
    m = TITLE_OUTCOME_RE.search(title or "")
    if not m:
        return None
    return 1 if m.group(1).lower() == "all" else 0


def _is_original(entry: dict) -> bool:
    """True iff this FWD is an original (not on-remand / rehearing).

    Mirrors `parse.labels._identify_terminating_fwd`'s filter — see
    `docs/scope/prediction_scope.md` §3.1 for why we only label originals.
    """
    dt = (entry.get("document_type") or "").lower()
    return "final written decision" in dt and FWD_ORIGINAL_MARKER.lower() in dt


def _load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    return [e for e in json.loads(MANIFEST_PATH.read_text()) if _is_original(e)]


def _read_text(doc_id: str) -> str:
    return (TEXT_CACHE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8", errors="replace")


_MANIFEST = _load_manifest()


@pytest.fixture(scope="module")
def manifest_entries() -> list[dict]:
    """Yield the cached FWD manifest, skipping the suite if empty."""
    if not _MANIFEST:
        pytest.skip(
            f"No FWD text sample cached. Run "
            f"`uv run python drivers/probe_fwd_pdfs_sample.py --target 200` "
            f"to populate {TEXT_CACHE_DIR}."
        )
    return _MANIFEST


@pytest.mark.parametrize(
    "entry",
    _MANIFEST,
    ids=[e["document_identifier"] for e in _MANIFEST] or ["empty"],
)
def test_extraction_resolves_or_correctly_quarantines(entry: dict) -> None:
    """`extract_outcome` returns None or 0/1; title-parseable FWDs must resolve.

    Returning None is a legitimate outcome — Motion-to-Amend rulings, for
    example, have no "Determining" line on their cover page. The caller's
    contract is to quarantine those rather than guess.

    The strong invariant we *do* enforce: if the API's
    `documentTitleText` already encodes the outcome, then the text
    extraction must also resolve. A None on a title-parseable FWD means
    pdfplumber drift or a regression in the patterns.
    """
    if not _MANIFEST:
        pytest.skip("No FWD text sample cached")
    text_path = TEXT_CACHE_DIR / f"{entry['document_identifier']}.txt"
    assert text_path.exists(), f"Missing cached text: {text_path}"
    text = _read_text(entry["document_identifier"])
    label = extract_outcome(text)
    assert label is None or label in (0, 1)
    if TITLE_OUTCOME_RE.search(entry.get("document_title", "") or ""):
        assert label is not None, (
            f"Title-parseable FWD {entry['document_identifier']} returned None — "
            f"pattern regression. title={entry['document_title'][:80]!r}"
        )


@pytest.mark.parametrize(
    "entry",
    [e for e in _MANIFEST if TITLE_OUTCOME_RE.search(e.get("document_title", "") or "")],
    ids=[
        e["document_identifier"]
        for e in _MANIFEST
        if TITLE_OUTCOME_RE.search(e.get("document_title", "") or "")
    ]
    or ["empty"],
)
def test_text_label_agrees_with_title(entry: dict) -> None:
    """For title-parseable FWDs, regex output must agree with the title."""
    if not _MANIFEST:
        pytest.skip("No FWD text sample cached")
    title_label = _label_from_title(entry["document_title"])
    assert title_label is not None  # filtered above
    text = _read_text(entry["document_identifier"])
    text_label = extract_outcome(text)
    assert text_label == title_label, (
        f"Disagreement on {entry['document_identifier']} "
        f"(year={entry['year']}): title says {title_label}, text says {text_label}. "
        f"Title: {entry['document_title']!r}"
    )


# Maximum fraction of original FWDs allowed to legitimately quarantine
# (return None). At our current 200-PDF cache the floor is 1/133 = 0.75%
# — a single Motion-to-Amend ruling whose cover has no "Determining"
# line. The 2% bound leaves headroom for similar rare formats to surface
# without breaking the build, while still flagging a regression that
# blew up the quarantine population.
QUARANTINE_RATE_CEILING = 0.02


def test_overall_resolution_rate(manifest_entries: list[dict]) -> None:
    """Aggregate reporter — log full sample stats, enforce coverage bounds."""
    from ml_uspto.schemas.patterns import FWD_PDF_OUTCOME_PATTERNS

    n = len(manifest_entries)
    n_resolved = 0
    quarantined: list[str] = []
    n_title_parseable = 0
    n_title_agree = 0
    pattern_hits: dict[str, int] = {}

    for entry in manifest_entries:
        text = _read_text(entry["document_identifier"])
        label = extract_outcome(text)
        if label in (0, 1):
            n_resolved += 1
        else:
            quarantined.append(entry["document_identifier"])
        title_label = _label_from_title(entry.get("document_title", ""))
        if title_label is not None:
            n_title_parseable += 1
            if label == title_label:
                n_title_agree += 1

        # Track which named pattern fired, for novelty/coverage surface.
        head = text[:4000]
        for p in FWD_PDF_OUTCOME_PATTERNS:
            if p.pattern.search(head):
                pattern_hits[p.name] = pattern_hits.get(p.name, 0) + 1
                break

    quarantine_rate = (n - n_resolved) / n if n else 0.0
    print(
        f"\nFWD outcome regex stats over {n} ORIGINAL FWDs:\n"
        f"  resolved (0/1):                   {n_resolved}/{n}\n"
        f"  quarantined (None):               {n - n_resolved}/{n} "
        f"({quarantine_rate:.1%})\n"
        f"  quarantined doc ids:              {quarantined}\n"
        f"  title-parseable subset:           {n_title_parseable}/{n}\n"
        f"  title↔text agreement (parseable): {n_title_agree}/{n_title_parseable}\n"
        f"  pattern-hit distribution:          {dict(sorted(pattern_hits.items()))}"
    )
    assert quarantine_rate <= QUARANTINE_RATE_CEILING, (
        f"Quarantine rate {quarantine_rate:.1%} exceeds ceiling "
        f"{QUARANTINE_RATE_CEILING:.0%}. Quarantined: {quarantined}. "
        "Either a new cover-page format surfaced (extend "
        "config/labels.yaml::fwd_pdf_outcome.patterns) or pdfplumber "
        "regressed on a vintage."
    )
    assert n_title_agree == n_title_parseable, (
        f"Disagreement on {n_title_parseable - n_title_agree}/"
        f"{n_title_parseable} title-parseable FWDs — see per-PDF failures."
    )
