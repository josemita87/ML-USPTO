# Ingestion Pipeline — Systems Plan

**Date**: 2026-04-27
**Status**: design — plumbing landed in PR 1 (commit `97d11e3`); stages 1–6 not yet executed.
**Scope**: end-to-end ingestion from "no local data" to a feature-ready parquet keyed by `trialNumber`, joining proceedings + petition + patent + petition-text.

This plan consolidates two design passes from session 2026-04-27:
1. The 4-stage pipeline (proceedings → petition discovery → patents → join).
2. The PDF download + text-feature extension (stages 5–6).

> **PR 1 deltas vs. this plan** (kept here so the rest of the doc still reads as the original design):
> - The cache lives in `clients/local.py`, not `ingest/cache.py`. Local FS is treated as a backend client (sibling of `clients/s3.py`).
> - `ingest/rate_limit.py` was not created. The 5/10/20-second backoff is now `api.backoff_seconds` in `config/settings.yaml`, consumed by `clients/uspto.py`.
> - There is no `storage/` directory. File I/O primitives moved into `clients/local.py` alongside the cache.
> - A new `src/ml_uspto/paths.py` is the single source of truth for filesystem paths (`PROJECT_ROOT`, config-file paths, `raw_dir()`, `processed_dir()`, etc.). All modules import it instead of building paths inline.
> - Eight new Pydantic seam models shipped in `schemas/models.py` (Petition, QuarantineEntry, Patent, PatentFeatures, JoinedTrial, PdfFetchManifestRow, PetitionTextDoc, PetitionTextFeatures).
>
> See CLAUDE.md hard rules #1 and #5 for the why.

Authoritative companion docs: `../api/proceedings.md`, `../api/patents.md`, `../api/api_feature_map.md`, `../api/rate_limits.md`, `../scope/prediction_scope.md`.

---

## 1. Context

### What we're building

A repeatable, resumable pipeline that produces:
- **`trials_joined.parquet`** — one row per trial, structured features only (proceedings + petition pointer + patent T₀-aggregated features). Quarantined trials excluded.
- **`petition_features.parquet`** — one row per trial, Tier 1 + Tier 2 petition-text features. Joins onto the above.
- **`petition_quarantine.parquet`** — separate file for trials where `pick_petition()` returned None (~1.3% empirically). Not joined; kept for audit.

Output feeds the (out-of-scope here) feature engineering layer.

### Existing code we reuse

- `parse/engine.py` — model-agnostic flatten driven by `config/parsers/<surface>.yaml`.
- `parse/petition_picker.py` — canonical, regression-tested. Don't redesign.
- `parse/preprocessing.py` — label construction (`cancelled` per scope §3).
- `parse/admissibility.py` — T₀ filter at trial-doc level.
- `clients/uspto.py` — has all six methods needed: `search_proceedings_post`, `search_documents_post`, `search_decisions_post`, `download_decisions`, `get_application`, `get_trial_documents`. Missing `download_pdf` / `stream_pdf` for stage 5.
- `schemas/models.py` — `Proceeding`, `TrialDocument`, `DecisionData`, `FeatureRow`, `Prediction`, `ModelMetrics`, `AdmissibilityPartition`. New models added by this plan.
- `config/parsers/{proceedings,decisions}.yaml` — flatten configs.

### Out of scope (explicit)

- PDF text feature engineering (consumes the joined parquet — that's `features/`).
- Tier 3 NLP / embeddings on petition text.
- OCR fallback (probe 2026-04-27 confirmed 0% image-PDF rate across 50 stratified petitions; quarantine outliers).
- Exhibit-PDF parsing (only the petition body itself).
- AWS infra rollout — `clients/s3.py` already stubbed; cache layout below maps 1:1 to S3 keys when we migrate.

---

## 2. Pipeline stages

```
STAGE 1  Trial inventory                                   ~30 s
  call: POST /trials/proceedings/search  filter trialTypeCode=IPR
  out:  data/raw/proceedings/page_*.json
        data/processed/trials.parquet         (with `cancelled` label)

STAGE 2  Petition discovery (corpus-wide scan)             ~5 min
  call: POST /trials/documents/search
        filter documentCategory IN ["PETITION","Paper"]   ~3K calls, ~301K rows
  parse: group by trialNumber → pick_petition() per group
  out:  data/processed/petitions.parquet                   (~17.7K rows)
        data/processed/petition_quarantine.parquet         (~230 rows)

STAGE 3  Patent enrichment (per unique application)        ~3–4 h
  call: GET /applications/{appNum}            ~10–13K calls (dedup by app)
  parse: flatten static fields + T₀-filtered aggregator
  out:  data/raw/patents/{app}.json
        data/processed/patents_static.parquet
        data/processed/patents_features.parquet            (per (trial, app))

STAGE 4  Join                                              ~seconds
  in:   trials.parquet ⟕ petitions.parquet ⟕ patents_features.parquet
  out:  data/processed/trials_joined.parquet               (excludes quarantine)

STAGE 5  Petition PDF download                             ~10 h
  in:   petitions.parquet (petition_pdf_uri column)
  call: client.session.get(uri)   # X-API-Key required, see rate_limits.md §3
  out:  data/raw/petitions_pdf/{trial}.pdf                 (~70 GB, local-only)
        data/raw/petitions_pdf/_manifest.parquet

STAGE 6  Petition text + feature extraction                ~30 min CPU
  in:   data/raw/petitions_pdf/{trial}.pdf
  parse: pdfplumber.extract_text → Tier 0 doc → Tier 1+2 features
  out:  data/processed/petition_text/{trial}.json.gz       (~2 GB total)
        data/processed/petition_features.parquet           (~50 MB)
```

**Globally serial.** Burst=1 across the whole metadata bucket means stages 1–3 and 5 must run sequentially as separate processes. Stage 6 is CPU-bound; can parallelize if needed but the 30 min single-process estimate is fine.

**Total cold-run wall-clock**: ~13–14 h, dominated by stage 5 (PDF download). Warm re-run (everything cached): ~2 min.

---

## 3. Directory tree (additions vs current state)

```
src/ml_uspto/
  paths.py                            [DONE — central filesystem path resolver]
  clients/
    uspto.py                          [MODIFY — add download_pdf, stream_pdf]
    local.py                          [DONE — local-FS cache + I/O primitives, sibling of s3.py]
  ingest/
    fetch.py                          [REWRITE — stage 1/2/3/4 entrypoints]
    fetch_petition_pdfs.py            [NEW — stage 5 driver]
    schemas/
      enums.py                        [DONE — Stage enum (proceedings, documents_petition_scan, patents)]
  parse/
    petition_assembler.py             [NEW — group by trialNumber, run picker]
    patent_aggregator.py              [NEW — T₀-filtered aggregations]
    petition_text.py                  [NEW — stage 6 brain (one module)]
    schemas/
      enums.py                        [MODIFY — add Parser.PETITION_TEXT]
      constants.py                    [MODIFY — load text_patterns.yaml]
  schemas/
    models.py                         [DONE — 8 seam models added; see §6]

config/
  parsers/
    documents.yaml                    [NEW — petition-scan flatten config]
    patents.yaml                      [NEW — static-only patent paths]
  patents/
    event_codes.yaml                  [NEW — event-code prefix families]
  petitions/
    text_patterns.yaml                [NEW — Tier 1/2 regex + boilerplate]

scripts/
  run_ingest_proceedings.py           [NEW]
  run_ingest_petitions.py             [NEW]
  run_ingest_patents.py               [NEW]
  run_join.py                         [NEW]
  run_extract_petition_text.py        [NEW — chains stage 5 + 6]

tests/
  fixtures/
    petitions/
      IPR2024-XXXXX.pdf               [NEW — modern, has Sotera]
      IPR2023-YYYYY.pdf               [NEW — modern, no Sotera]
      IPR2014-ZZZZZ.pdf               [NEW — pre-2015 pdfplumber smoke]
      expected_features.yaml          [NEW — locked Tier 1/2 values]
  unit/
    test_petition_assembler.py        [NEW]
    test_patent_aggregator.py         [NEW]
    test_local.py                     [DONE — clients/local.py round-trip]
    test_petition_text_structural.py  [NEW]
    test_petition_text_substantive.py [NEW]
    test_petition_text_features.py    [NEW — round-trip]
```

No new top-level dirs. All adds land inside `ingest/`, `parse/`, `schemas/`, `clients/`, `config/` per CLAUDE.md (local FS is a client backend; there is no `storage/`).

---

## 4. File-by-file specs

### `clients/uspto.py` (modify)

Two new methods reusing `self.session` (auth header is mandatory per `rate_limits.md` §3):

```python
def download_pdf(self, uri: str, *, timeout: int = 120) -> bytes:
    """GET a petition PDF via the authenticated session. 429-aware retry."""

def stream_pdf(self, uri: str, dst: Path, *, timeout: int = 120) -> int:
    """Stream-download to dst.tmp, fsync, atomic-rename to dst. Returns bytes."""
```

`stream_pdf` writes atomically (`.tmp` then `os.replace`) so a Ctrl-C mid-download never leaves a half-written PDF that the idempotency check accepts. Both share the existing 5/10/20-second 429 backoff.

### `clients/local.py` *(DONE in PR 1)*

```python
def cache_path(bucket: str, key: str, *, root: Path | None = None) -> Path
def load_if_present(bucket: str, key: str, *, root: Path | None = None) -> dict | None
def save(bucket: str, key: str, payload: dict, *, root: Path | None = None) -> None
def iter_cached(bucket: str, *, root: Path | None = None) -> Iterator[tuple[str, dict]]
```

Reads/writes JSON under `paths.raw_dir() / <bucket> / <key>.json`. `bucket` is generic `str` — callers pass `Stage.PROCEEDINGS` etc. (StrEnum members are str subclasses). `load_if_present` is the idempotency check — every fetcher hits this before any HTTP call. S3 mapping later: `s3://<bucket>/raw/<key>.json` — same key shape, swap `clients/local.py` for `clients/s3.py`. Also hosts the file-I/O primitives (`save_parquet`, `load_parquet`, `save_json`, `load_json`).

### Rate limiting *(handled in `config/settings.yaml`, not a dedicated module)*

`api.backoff_seconds: [5, 10, 20]` in `config/settings.yaml`, consumed by `USPTOClient._get`/`_post`. The burst=1 global invariant is enforced by single-process serial scripts; if a future contributor wants to parallelize, the invariant must be reasserted (a lock or a single-writer queue). v1 has no lock.

### `ingest/fetch.py` (rewrite)

```python
def fetch_proceedings(client: USPTOClient) -> pd.DataFrame
def scan_petitions(client: USPTOClient, trials: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]
def fetch_patents(client: USPTOClient, trials: pd.DataFrame) -> pd.DataFrame
def join_all(trials: pd.DataFrame, petitions: pd.DataFrame, patents: pd.DataFrame) -> pd.DataFrame
```

Each paginates → caches → flattens via `parse.engine.flatten` → validates row-by-row into a Pydantic model → writes parquet. `join_all` left-joins trials ⟕ petitions ⟕ patents on `(trial_number, application_number)`, validates each row into `JoinedTrial`, **drops quarantined trials** (per session decision 2026-04-27).

### `ingest/fetch_petition_pdfs.py` (new) — stage 5 driver

```python
def fetch_all_petition_pdfs(
    *,
    petitions_parquet: Path = data/processed/petitions.parquet,
    pdf_dir: Path        = data/raw/petitions_pdf,
    manifest_path: Path  = data/raw/petitions_pdf/_manifest.parquet,
    client: USPTOClient | None = None,
) -> ManifestSummary
```

Iterates rows in deterministic order (by `trial_number`). Skips if `{trial}.pdf` already exists. On HTTP errors records the failure in the manifest with `http_status` set and continues — failed downloads quarantine rather than crash the run. No bare `requests`.

### `parse/petition_assembler.py` (new)

```python
def assemble_petitions(
    raw_doc_records: list[dict],
    trials: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]
```

Groups raw document records by `trialNumber`. Per group: `picked = pick_petition(group_rows)`. None → quarantine `(trial_number, n_candidates, sample_titles)`. Otherwise build a `Petition` model from `picked["documentData"]` only — **never `picked["trialMetaData"]`** (documented leakage hazard). Cross-checks `documentFilingDate == trials.petition_filing_date`; logs warning on mismatch but the proceedings T₀ wins.

### `parse/patent_aggregator.py` (new) — canonical T₀-filter site

```python
def aggregate(
    raw_patent_records: list[dict],
    trials: pd.DataFrame,
) -> pd.DataFrame
```

For each `(trialNumber, applicationNumberText)` pair:
1. Look up the cached patent record by app number.
2. T₀-filter the dated bags using `petitionFilingDate`:
   - `eventDataBag`: drop entries with `eventDate ≥ T₀`, drop all `TRIAL*` codes
   - `assignmentBag`: drop `assignmentRecordedDate ≥ T₀`
   - `parentContinuityBag`: drop `parentApplicationStatusCode` field entirely (status-now is disallowed)
3. Aggregate per `patents.md` table: `n_events_pre_t0`, family counts via `event_codes.yaml`, `cpc_section`, PTA passthrough, etc.
4. Emit `PatentFeatures` row per pair.

**Module docstring states the contract**: `features/` consumes `PatentFeatures`, never the raw `Patent` flatten. That's leakage discipline as code.

### `parse/petition_text.py` (new) — stage 6

One module, two private namespaces (`_structural`, `_substantive`). Decision rationale: petition has a regulated 37 CFR §42.104 layout; section headers and regexes share enough taxonomy that splitting forces threading the YAML loader, `pdfplumber` import, and `PetitionTextFeatures` constructor through both files. Matches the `petition_picker.py` "one well-documented module per stage" idiom.

```python
def extract_text(pdf_path: Path) -> PetitionTextDoc
def write_text_doc(doc: PetitionTextDoc, dst: Path) -> None
def load_text_doc(path: Path) -> PetitionTextDoc
def compute_features(doc: PetitionTextDoc, petition_row: Petition) -> PetitionTextFeatures
def build_features_parquet(*, text_dir: Path, out_path: Path, petitions_parquet: Path) -> None
```

Private extractors (one per feature, all `(text: str, pages: list[str]) -> int | bool`):

```
Tier 1 — structural / volumetric
  _word_count_from_certificate     # last-page Certificate of Word Count
  _claims_challenged
  _n_grounds
  _n_exhibits
  _n_prior_art_refs
  _n_expert_declarations

Tier 2 — statutory + procedural posture
  _n_grounds_by_statute            # 102/103
  _has_sotera_stipulation
  _mentions_fintiv_factors
  _discloses_prior_iprs_same_patent
  _n_real_parties_in_interest
  _claim_construction_disputed_terms
```

Failure mode: if a section header doesn't match, return `0` / `False` — NOT NaN. Word count is the one exception: `None` if the certificate is missing (~1–3% empirically). Lossy fallback (`len(re.findall(r"\w+", text))`) rejected — leakage discipline benefits more from honest missingness than a biased proxy.

### `config/petitions/text_patterns.yaml` (new)

Per CLAUDE.md hard rule #1 — every regex / boilerplate phrase / threshold goes here, not inline. Loaded once into a frozen dataclass at import. Top-level keys:

- `section_headers` — claims_challenged, grounds, exhibit_list, word_count_certificate
- `claims_challenged_list.enumeration` — captures e.g. "Claims 1, 3-7, and 12"
- `grounds_enumeration.pattern` — counts distinct "Ground N" headers (dedup by N)
- `statutory_grounds.{102,103}` — multi-pattern OR
- `prior_art_ref.{patent_number, publication_number}`
- `expert_declaration.exhibit_label`
- `sotera_stipulation.phrases` — three observed phrasings (boolean OR)
- `fintiv_factors.phrases`
- `prior_iprs_same_patent.{section_header, ipr_reference}`
- `real_parties_in_interest.section_header`
- `claim_construction.{section_header, disputed_term.pattern}`
- `limits.{pdf_max_pages, word_count_search_pages}`

### `config/parsers/documents.yaml` (new)

Flatten config for stage 2's POST response. Maps `trialNumber` and `documentData.*` paths only. **No `trialMetaData.*` paths** — the YAML enforces the leakage rule statically.

### `config/parsers/patents.yaml` (new)

Static-only paths from `patents.md` field table (Bibliographic + PTA + cpcClassificationBag). **No event/assignment/continuity dated paths** — those go through the aggregator. This split is the schema-level expression of `patents.md` A5: dated bags can't reach the engine at all.

### `config/patents/event_codes.yaml` (new)

```yaml
families:
  office_actions: ["CTNF", "CTFR", "CTAV", "CTRS"]
  ids:           ["IDS", "IDSC", "WIDS"]
  maintenance:   ["M*"]
  trial:         ["TRIAL*"]   # banned — leakage
disallowed_prefixes: ["TRIAL"]
```

### Driver scripts

Each is ~10 lines: instantiate `USPTOClient`, call the corresponding `ingest.fetch.*` function, print row counts. No business logic.

```python
# scripts/run_ingest_proceedings.py
from ml_uspto.clients.uspto import USPTOClient
from ml_uspto.ingest.fetch import fetch_proceedings

if __name__ == "__main__":
    df = fetch_proceedings(USPTOClient())
    print(f"trials: {len(df)} rows, {df['cancelled'].sum()} positives")
```

`run_extract_petition_text.py` chains stages 5+6 with CLI flags `--skip-fetch`, `--skip-features`, `--text-only`.

---

## 5. On-disk artifact layout

```
data/
  raw/
    proceedings/
      page_0000000.json                  # ~180 files, ~100 records each
      page_0000100.json
      …
    documents_petition_scan/
      page_0000000.json                  # ~3000 files, ~100 records each
      …
    patents/
      14709428.json                      # one file per applicationNumberText
      15234567.json                      # ~10–13K files
      …
    petitions_pdf/                       # local-only; never push to S3
      IPR2024-00123.pdf                  # ~4 MB avg, ~70 GB total
      IPR2024-00124.pdf
      …
      _manifest.parquet                  # PdfFetchManifestRow rows
  processed/
    trials.parquet                       # one row per trial, with label
    petitions.parquet                    # one row per trial-with-petition
    petition_quarantine.parquet          # ~230 rows; review manually
    patents_static.parquet               # one row per application
    patents_features.parquet             # one row per (trial, app), T₀-filtered
    trials_joined.parquet                # final structured frame (excludes quarantine)
    petition_text/                       # Tier 0 — kept in S3 too
      IPR2024-00123.json.gz              # ~50–150 KB compressed
      …
    petition_features.parquet            # Tier 1+2 — one row per trial
```

### Local vs S3 split (free-tier decision, session 2026-04-27)

| Artifact | Local | S3 | Why |
|---|---|---|---|
| `data/raw/petitions_pdf/*.pdf` | yes (~70 GB) | **no** | 70 GB > AWS free-tier 5 GB cap; PDFs reproducible from URIs |
| `data/raw/petitions_pdf/_manifest.parquet` | yes | yes (small) | Audit trail |
| `data/raw/{proceedings,documents_petition_scan,patents}/*.json` | yes (~5 GB) | yes | Idempotency cache; cheap to replicate |
| `data/processed/petition_text/*.json.gz` | yes (~2 GB) | yes | Tier 0 source-of-truth for re-extraction |
| `data/processed/*.parquet` | yes (~150 MB) | yes | Feature snapshots |

**S3 mapping**: `s3://<bucket>/raw/<key>.json` is a 1:1 swap of cache backend. `clients/local.py` and a future `clients/s3.py` expose the same `cache_path` / `load_if_present` / `save` / `iter_cached` shape; the fetcher gets a backend flag and stays oblivious to which one it's hitting.

---

## 6. Schema boundaries

Per CLAUDE.md hard rule #5, no `dict[str, Any]` flows between stages.

| Seam | Pydantic model (in `schemas/models.py`) | Produced by | Consumed by |
|---|---|---|---|
| Proceedings raw row → trial frame | `Proceeding` *(exists)* | `ingest.fetch.fetch_proceedings` after `flatten()` | downstream |
| Document raw row (picked) → petition frame | `Petition` *(new)* | `parse.petition_assembler.assemble_petitions` | stage 4 join, stage 5 download |
| Trials with no picked petition | `QuarantineEntry` *(new)* | same | manual review |
| Patent raw → static frame | `Patent` *(new)* | `ingest.fetch.fetch_patents` after `flatten()` | analysis-only |
| Patent raw + T₀ → feature row | `PatentFeatures` *(new)* | `parse.patent_aggregator.aggregate` (canonical T₀-filter) | stage 4 join |
| Final structured frame | `JoinedTrial` *(new)* | `ingest.fetch.join_all` | features layer |
| PDF fetch result | `PdfFetchManifestRow` *(new)* | `clients.uspto.stream_pdf` via stage 5 | retry/audit |
| Tier 0 text artifact | `PetitionTextDoc` *(new)* | `parse.petition_text.extract_text` | `compute_features`, future Tier 3 |
| Petition text features | `PetitionTextFeatures` *(new)* | `parse.petition_text.compute_features` | features layer (left-join on trial_number) |

### New model field summaries

```python
class Petition:
    trial_number, petition_document_id, petition_title, petition_number,
    petition_filing_date_doc, petition_pdf_uri, petition_category

class QuarantineEntry:
    trial_number, reason, n_candidates, sample_titles

class Patent:                         # static-only flatten
    application_number, filing_date, effective_filing_date, application_type,
    entity_size, first_inventor_to_file, n_inventors, cpc_codes, uspc_class_subclass,
    pta_a_delay, pta_b_delay, pta_c_delay, pta_total, pta_applicant_delay

class PatentFeatures:                 # T₀-aggregated, per (trial, app)
    trial_number, application_number, cpc_section, n_events_pre_t0,
    prosecution_span_days, n_office_actions, n_ids_filings,
    n_assignments_pre_t0, n_distinct_assignees_pre_t0, days_since_last_assignment,
    n_parent_applications, days_grant_to_petition

class JoinedTrial:                    # the structured feature-ready boundary
    # all Proceeding fields + label
    # petition seam: petition_pdf_uri, petition_filing_date_doc
    # patent seam: patent_features: PatentFeatures | None

class PdfFetchManifestRow:
    trial_number, bytes, sha256, http_status, fetched_at, error

class PetitionTextDoc:                # Tier 0
    trial_number, page_count, char_count, pages: list[str],
    pdfplumber_version, extracted_at

class PetitionTextFeatures:           # Tier 1 + Tier 2
    trial_number,
    petition_word_count, petition_page_count, n_claims_challenged, n_grounds,
    n_exhibits, n_prior_art_refs, n_expert_declarations,
    n_grounds_102, n_grounds_103, has_sotera_stipulation,
    mentions_fintiv_factors, discloses_prior_iprs_same_patent,
    n_real_parties_in_interest, claim_construction_disputed_terms,
    text_doc_sha256, extracted_at
```

---

## 7. Order of operations

Each step independently runnable + verifiable.

1. ✅ **Cache + I/O primitives** — landed as `clients/local.py` (PR 1). Unit-tested in `tests/unit/test_local.py`.
2. ✅ **Pydantic seam models** — eight models added to `schemas/models.py` (PR 1).
3. **Land `config/parsers/documents.yaml` + `config/parsers/patents.yaml`**. Verify by running `flatten()` against fixtures.
4. **Land `config/patents/event_codes.yaml` + `config/petitions/text_patterns.yaml`** + extend `parse/schemas/constants.py`. Verify constants compile.
5. **Add `download_pdf` + `stream_pdf` to `clients/uspto.py`** — manual probe against ~5 known petition URIs to confirm auth and atomic rename.
6. **Rewrite `ingest/fetch.py::fetch_proceedings`**. Run cold via `scripts/run_ingest_proceedings.py`; verify ~18K rows in `trials.parquet`; confirm cache hits on second run.
7. **Add `parse/petition_assembler.py`** + unit tests (~50 fixtures, 5 quarantine cases).
8. **Add `ingest/fetch.py::scan_petitions`**. Run via `scripts/run_ingest_petitions.py`; verify ~17.7K petitions + ~230 quarantine; spot-check 10 picks against the picker regression suite.
9. **Add `parse/patent_aggregator.py`** + unit test on the IPR2022-01002 probe payload (`patents.md`) with hand-computed expected values, including `TRIALFWD` event drop.
10. **Add `ingest/fetch.py::fetch_patents`**. 100-app subset first; then full run.
11. **Add `ingest/fetch.py::join_all`**. Verify final parquet row count = `len(trials) - len(quarantine)`.
12. **Implement `ingest/fetch_petition_pdfs.py`** — 50-PDF slice end-to-end, confirm idempotency.
13. **Implement `parse/petition_text.py`** top-down: `extract_text` → `_structural` (lock 3 fixture Tier 1 numbers) → `_substantive` (lock booleans) → `compute_features` → `build_features_parquet`.
14. **Wire `scripts/run_extract_petition_text.py`** + fixture regression tests.
15. **Full Stage 5 run (~10h)**, then Stage 6 (~30 min CPU).

---

## 8. Open questions

Resolved in session 2026-04-27:
- ✅ **Quarantine policy**: kept in `petition_quarantine.parquet`, **excluded from joined frame** (no `in_quarantine` flag in `JoinedTrial`).
- ✅ **Static vs features parquet**: keep both. Per-app static parquet de-duplicates the patent universe; per-(trial, app) features parquet handles joinder.
- ✅ **Pagination strategy**: linear `offset` for stage 2; add `rangeFilters` partitioning only if deep-offset caps out empirically.
- ✅ **OCR**: skip. Probe confirmed 0% image-PDF rate across 50 stratified petitions 2012–2024.
- ✅ **PDF parser**: `pdfplumber` (better claim-chart layout, ~3× slower than `pypdf`; speed irrelevant at 30 min CPU).
- ✅ **PDF storage**: local-FS only; never push to S3 (free-tier 5 GB cap).

Still open:
1. **Word-count fallback when Certificate of Word Count is unparseable.** Picking honest `None` over `len(re.findall(r"\w+", text))` proxy. Revisit if `None` rate exceeds ~5%.
2. **`n_real_parties_in_interest` parsing.** §42.8(b)(1) phrasings vary — comma-separated paragraph vs numbered list vs "Petitioner [X] is the sole real party". Defer until after looking at 20–30 real sections; flag as low-confidence v1 extractor.
3. **mtime-based vs hash-based rebuild for `petition_features.parquet`.** Picking mtime for v1 (local-FS only); revisit when feature parquet is computed in CI.
4. **Quarantine rescue** (~230 trials). Skip in v1; revisit only if class balance shifts or coverage matters for representativeness.

---

## 9. Critical files

- `src/ml_uspto/ingest/fetch.py` — stages 1–4 entrypoints (rewrite)
- `src/ml_uspto/ingest/fetch_petition_pdfs.py` — stage 5 driver
- `src/ml_uspto/parse/petition_assembler.py` — picker → frame
- `src/ml_uspto/parse/patent_aggregator.py` — canonical T₀-filter site
- `src/ml_uspto/parse/petition_text.py` — stage 6 brain
- `src/ml_uspto/clients/uspto.py` — add `download_pdf` / `stream_pdf`
- `src/ml_uspto/schemas/models.py` — 8 new models
- `config/petitions/text_patterns.yaml` — Tier 1/2 patterns
- `config/patents/event_codes.yaml` — event prefix families
- `config/parsers/{documents,patents}.yaml` — flatten configs
