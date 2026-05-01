# Ingestion Pipeline — Systems Plan

> **Historical note.** This was the implementation plan while the pipeline was being built. It is not the current operational reference. For the live DAG, persistence model, and FWD-text label fallback, use `../ops/refresh_lifecycle.md`; for current feature scope, use `../scope/prediction_scope.md`.

**Date**: 2026-04-27 (v1 scope narrowed 2026-04-28; storage backend abstracted 2026-04-29; FWD-PDF label fallback scaffolded 2026-04-29)
**Status**: stages 1–4 plumbing landed end-to-end. v1 deliverable `data/processed/joined_trials.parquet` produced. Outstanding: full patents cold-run (~120/~10K apps cached); FWD-outcome label resolution — regex + capture map for the FWD-PDF cover page now live in `config/labels.yaml::fwd_pdf_outcome` + `schemas/constants.py::FWD_PDF_*` (validated on 25 stratified FWDs), but the FWD-PDF download driver (under bucket `Stage.DECISION_PDFS`) and the `preprocess` fallthrough that runs the regex are not yet wired.
**Scope (v1)**: end-to-end ingestion from "no local data" to a feature-ready parquet keyed by `trialNumber`, joining proceedings + petition pointer + patent. **Metadata-only.** PDF download and petition-text feature extraction (the original stages 5–6) are deferred to v2 — see `### v1 cut` below.

The original plan covered six stages. v1 stops at stage 4 (the join). This document still describes the 4-stage metadata pipeline; deleted text covers what v1 deliberately omits.

> **PR 1 deltas vs. this plan** (kept here so the rest of the doc still reads as the original design):
> - The cache lives in `clients/local.py`, not `ingest/cache.py`. Local FS is treated as a backend client (sibling of `clients/s3.py`).
> - `ingest/rate_limit.py` was not created. The 5/10/20-second backoff is now `api.backoff_seconds` in `config/settings.yaml`, consumed by `clients/uspto.py`.
> - There is no `storage/` directory. File I/O primitives moved into `clients/local.py` alongside the cache.
> - A new `src/ml_uspto/paths.py` resolves filesystem roots (`PROJECT_ROOT`, config-file paths, `raw_dir()`, `processed_dir()`). Per-frame parquet helpers were dropped 2026-04-29; frame keys now live in `schemas.enums.Frame` and routing goes through the `Storage` Protocol.
> - Eight new Pydantic seam models shipped in `schemas/models.py` (Petition, QuarantineEntry, Patent, PatentFeatures, JoinedTrial, PdfFetchManifestRow, PetitionTextDoc, PetitionTextFeatures); `JoinReport` added 2026-04-29.
>
> **Storage refactor delta (2026-04-29)**:
> - `clients/storage.py` defines the backend-agnostic `Storage` Protocol: `load_frame/save_frame` (no bucket — single tabular namespace, keyed by `Frame`), `load_object/save_object/iter_objects` (bucket+key for raw JSON cache), and `load_blob/save_blob/has_blob/blob_path` (bucket+key+ext for binary blobs, e.g. PDFs).
> - `clients/local.py` is now a `LocalStorage` class implementing that Protocol. Module-level functions (`cache_path`, `load_if_present`, `save`, `save_parquet`, …) are gone.
> - `ingest/fetch.py` and `parse/joiner.py` take `storage: Storage` as the first argument. Drivers instantiate `LocalStorage()` and pass it down. Tests inject a `tmp_path`-rooted `LocalStorage` instead of monkeypatching module functions.
>
> See CLAUDE.md hard rules #1 and #5 for the why.

Authoritative companion docs: `../api/proceedings.md`, `../api/patents.md`, `../api/api_feature_map.md`, `../api/rate_limits.md`, `../scope/prediction_scope.md`.

---

## 1. Context

### What we're building

A repeatable, resumable pipeline that produces:
- **`trials_joined.parquet`** — one row per trial, structured features only (proceedings + petition pointer + patent T₀-aggregated features). Quarantined trials excluded.
- **`petition_quarantine.parquet`** — separate file for trials where `pick_petition()` returned None (~1.3% empirically). Not joined; kept for audit.

Output feeds the (out-of-scope here) feature engineering layer.

### v1 cut

v1 is **metadata-only**. The following are deliberately deferred to v2:
- Petition PDF download (~70 GB, ~10 h serial via the file-wrapper bucket).
- PDF text extraction with `pdfplumber` and Tier 1 / Tier 2 feature engineering (`petition_features.parquet`).

Rationale: the metadata pipeline alone gives us proceedings + petition pointer + patent file-wrapper features — enough surface area to train and evaluate a structured baseline. PDF text features can be layered on later without rewiring stages 1–4. The 8 Pydantic seam models added in PR 1 include `PdfFetchManifestRow`, `PetitionTextDoc`, and `PetitionTextFeatures`; these stay in `schemas/models.py` as forward-compatible stubs but no v1 code consumes them.

### Existing code we reuse

- `parse/engine.py` — model-agnostic flatten driven by `config/parsers/<surface>.yaml`.
- `parse/petition_picker.py` — canonical, regression-tested. Don't redesign.
- `parse/preprocessing.py` — label construction (`cancelled` per scope §3).
- `parse/admissibility.py` — T₀ filter at trial-doc level.
- `clients/uspto.py` — has all six methods needed for v1: `search_proceedings_post`, `search_documents_post`, `search_decisions_post`, `download_decisions`, `get_application`, `get_trial_documents`. (PDF-download methods `download_pdf` / `stream_pdf` deferred with stage 5.)
- `schemas/models.py` — `Proceeding`, `TrialDocument`, `DecisionData`, `FeatureRow`, `Prediction`, `ModelMetrics`, `AdmissibilityPartition`. New models added by this plan.
- `config/parsers/{proceedings,decisions}.yaml` — flatten configs.

### Out of scope (explicit, v1)

- **Petition PDF download.** Deferred to v2 (~70 GB, ~10 h serial through the file-wrapper bucket).
- **Petition-text feature extraction** — Tier 1 (structural / volumetric counts) and Tier 2 (statutory + procedural posture flags). Deferred with the PDF download.
- Tier 3 NLP / embeddings on petition text. Always deferred (post-v2).
- OCR fallback. Deferred with the PDFs (probe 2026-04-27 confirmed 0% image-PDF rate across 50 stratified petitions; not a v1 concern either way).
- Exhibit-PDF parsing.
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
        filter documentData.documentCategory IN ["PETITION","Paper"]
                                                    ~3K calls, ~323K rows
  parse: group by trialNumber → pick_petition() per group
  out:  data/processed/petitions.parquet                   (~17.8K rows)
        data/processed/petition_quarantine.parquet         (~230 rows; ~1.3% of trials)

STAGE 3  Patent enrichment (per unique application)        ~3–4 h
  call: GET /applications/{appNum}            ~10–13K calls (dedup by app)
  parse: flatten static fields + T₀-filtered aggregator
  out:  data/raw/patents/{app}.json
        data/processed/patents_static.parquet
        data/processed/patents_features.parquet            (per (trial, app))

STAGE 4  Join                                              ~seconds
  in:   trials.parquet ⟕ petitions.parquet ⟕ patents_features.parquet
  out:  data/processed/trials_joined.parquet               (excludes quarantine)

# v2 (deferred): STAGE 5 (PDF download) + STAGE 6 (pdfplumber text + Tier 1/2
# features). See `### v1 cut` in §1.
```

**Globally serial.** Burst=1 across the whole metadata bucket means stages 1–3 must run sequentially as separate processes.

**Total cold-run wall-clock (v1)**: ~3–4 h, dominated by stage 3 (per-application patent fetches). Warm re-run (everything cached): ~30 s.

---

## 3. Directory tree (additions vs current state)

```
src/ml_uspto/
  paths.py                            [DONE — central filesystem path resolver]
  clients/
    uspto.py                          [DONE for v1 — six methods needed]
    local.py                          [DONE — local-FS cache + I/O primitives, sibling of s3.py]
  ingest/
    fetch.py                          [REWRITE — stage 1/2/3/4 entrypoints]
    schemas/
      enums.py                        [DONE — Stage enum (proceedings, documents_petition_scan, decisions, patents, decision_pdfs)]
      constants.py                    [DONE — STAGE_RECORDS_KEY, PETITION_SCAN_CATEGORIES]
  parse/
    petition_assembler.py             [DONE — group by trialNumber, run picker]
    patent_aggregator.py              [NEW — T₀-filtered aggregations]
  schemas/
    models.py                         [DONE — 8 seam models added; PdfFetchManifestRow,
                                       PetitionTextDoc, PetitionTextFeatures kept dormant for v2]

config/
  parsers/
    patents.yaml                      [DONE — proceedings + decisions + documents flatten configs]
  petition_picker.yaml                [DONE — title/blacklist/scan filter/quarantine config]

drivers/
  run_ingest_proceedings.py           [DONE]
  run_ingest_decisions.py             [DONE — added when label assembly required the decisions surface]
  run_ingest_petitions.py             [DONE]
  run_ingest_patents.py               [DONE]
  run_join.py                         [DONE]

tests/
  unit/
    test_local.py                     [DONE]
    test_petition_picker.py           [DONE]
    test_petition_assembler.py        [DONE]
    test_patent_aggregator.py         [NEW]
    test_schemas.py                   [DONE]
```

**v2 additions** (deferred): `clients/uspto.py::download_pdf` / `stream_pdf`; `ingest/fetch_petition_pdfs.py`; `parse/petition_text.py`; `config/petitions/text_patterns.yaml`; `drivers/run_extract_petition_text.py`; petition fixture PDFs + `test_petition_text_*.py`.

No new top-level dirs. All adds land inside `ingest/`, `parse/`, `schemas/`, `clients/`, `config/` per CLAUDE.md (local FS is a client backend; there is no `storage/`).

---

## 4. File-by-file specs

### `clients/uspto.py` *(no v1 changes)*

The existing six methods cover stages 1–4. PDF-download methods (`download_pdf` / `stream_pdf`) are part of the v2 stage-5 work and not added in v1.

### `clients/storage.py` + `clients/local.py` *(rewritten 2026-04-29)*

Backend-agnostic Protocol in `clients/storage.py`:

```python
class Storage(Protocol):
    def load_frame(self, key: Frame, *, columns: list[str] | None = None) -> pd.DataFrame: ...
    def save_frame(self, df: pd.DataFrame, key: Frame) -> None: ...
    def load_object(self, bucket: str, key: str) -> dict | None: ...
    def save_object(self, bucket: str, key: str, payload: dict) -> None: ...
    def iter_objects(self, bucket: str) -> Iterator[tuple[str, dict]]: ...
    # binary blobs (e.g. PDFs); the LocalStorage impl exposes these methods —
    # they will move onto the Protocol when an S3 backend lands.
    def blob_path(self, bucket: str, key: str, ext: str) -> Path: ...
    def has_blob(self, bucket: str, key: str, ext: str) -> bool: ...
    def save_blob(self, bucket: str, key: str, ext: str, payload: bytes) -> None: ...
    def load_blob(self, bucket: str, key: str, ext: str) -> bytes | None: ...
```

`LocalStorage` (in `clients/local.py`) implements it: frames go to `processed_root / <frame>.parquet`, objects to `raw_root / <bucket> / <key>.json`, blobs to `raw_root / <bucket> / <key>.<ext>`. Buckets are `Stage` enum values (StrEnum, str subclass); frame keys are `Frame` enum values. `load_object` is the idempotency check — every fetcher hits this before any HTTP call. S3 mapping later: a future `clients/s3.py::S3Storage` implements the same Protocol against `s3://<bucket>/...` — drivers swap which class they instantiate, nothing else changes.

### Rate limiting *(handled in `config/settings.yaml`, not a dedicated module)*

`api.backoff_seconds: [5, 10, 20]` in `config/settings.yaml`, consumed by `USPTOClient._get`/`_post`. The burst=1 global invariant is enforced by single-process serial scripts; if a future contributor wants to parallelize, the invariant must be reasserted (a lock or a single-writer queue). v1 has no lock.

### `ingest/fetch.py` *(takes `storage: Storage` first arg, 2026-04-29)*

```python
def fetch_proceedings(storage: Storage, client: USPTOClient, *, page_size: int, max_pages: int | None = None) -> pd.DataFrame
def fetch_decisions(storage: Storage, client: USPTOClient, *, page_size: int, max_pages: int | None = None) -> pd.DataFrame
def fetch_petitions(storage: Storage, client: USPTOClient, *, page_size: int, max_pages: int | None = None) -> Iterator[dict]
def fetch_patents(storage: Storage, client: USPTOClient, application_numbers: Iterable[str], *, page_size: int, max_apps: int | None = None) -> Iterator[PatentFetchResult]
```

Each paginates (caching pages via `storage.load_object` / `storage.save_object`) → flattens via `parse.engine.flatten` → saves the resulting DataFrame via `storage.save_frame(df, Frame.<X>)`. `fetch_petitions` is a generator; `parse.petition_assembler.assemble_petitions` consumes it. `fetch_patents` yields per-app `PatentFetchResult` records (success or quarantine) and caches each raw file wrapper under bucket `Stage.PATENTS`.

The join lives in `parse/joiner.py`, not `ingest/fetch.py` (per CLAUDE.md "pagination + flatten are separate concerns" — `join_all` is pure post-processing, no HTTP):

```python
def join_all(
    storage: Storage,
    *,
    trials: pd.DataFrame,
    decisions: pd.DataFrame,
    petitions: pd.DataFrame,
    petition_quarantine: pd.DataFrame,
    patent_quarantine: pd.DataFrame,
) -> tuple[pd.DataFrame, JoinReport]
```

Internally: `preprocess(trials, decisions)` → labeled trials with `cancelled` (status-based first; FWD-status falls through to the decisions-side outcome lookup, with the planned FWD-PDF cover-page regex as final fallback) → inner-merge with `petitions` (drops petition-quarantined trials) → per-row `aggregate_patent` via `storage.load_object(Stage.PATENTS, app)` + proceedings-side `days_grant_to_petition` patch. Returns the joined frame plus a `JoinReport` audit struct.

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

> **v2 (deferred): `parse/petition_text.py` + `config/petitions/text_patterns.yaml`** — Tier 1 (structural / volumetric) + Tier 2 (statutory / procedural posture) feature extraction over `pdfplumber`-extracted petition text. v1 ships the seam models (`PetitionTextDoc`, `PetitionTextFeatures`) but no consumer.

### `config/parsers/patents.yaml`

Single-file flatten config keyed by `Parser` enum members. v1 has three surfaces in flight:

- `proceedings` *(DONE)* — proceedings-row paths.
- `decisions` *(DONE)* — decisions-row paths (kept for future label cross-checks; v1 reads only the proceedings status).
- `documents` *(DONE)* — `trialNumber` + `documentData.*` paths only. **No `trialMetaData.*`** — encodes the leakage rule statically.
- `patents` *(NEW)* — to be added with stage 3. Static-only paths from `patents.md` field table (Bibliographic + PTA + cpcClassificationBag). **No event/assignment/continuity dated paths** — those go through the aggregator. This split is the schema-level expression of `patents.md` A5: dated bags can't reach the engine at all.

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

Each is ~10–20 lines: parse args (`--max-pages` for smoke runs), configure logging, instantiate `USPTOClient`, call the corresponding `ingest.fetch.*` function, write the parquet, print row counts. No business logic.

v1 drivers: `run_ingest_proceedings.py` *(DONE)*, `run_ingest_decisions.py` *(DONE — required by `preprocess` for the FWD branch of the label ladder)*, `run_ingest_petitions.py` *(DONE — depends on `Frame.TRIALS` for the proceedings-side T₀ cross-check)*, `run_ingest_patents.py` *(DONE — depends on `Frame.TRIALS.application_number`)*, `run_join.py` *(DONE)*. Each driver instantiates `LocalStorage()` and threads it into the corresponding `ingest.fetch.*` / `parse.joiner.*` call.

---

## 5. On-disk artifact layout

```
data/
  raw/
    proceedings/
      page_0000.json                     # ~180 files, ~100 records each
      page_0001.json
      …
    documents_petition_scan/
      page_0000.json                     # ~3.2K files, ~100 records each
      …
    patents/
      14709428.json                      # one file per applicationNumberText
      15234567.json                      # ~10–13K files
      …
  processed/
    trials.parquet                       # Frame.TRIALS — one row per trial (proceedings spine)
    decisions.parquet                    # Frame.DECISIONS — one row per decision paper
    petitions.parquet                    # Frame.PETITIONS — one row per trial-with-petition
    petition_quarantine.parquet          # Frame.PETITION_QUARANTINE — ~230 rows; review manually
    patents.parquet                      # Frame.PATENTS — static-only flatten, one row per application
    patent_quarantine.parquet            # Frame.PATENT_QUARANTINE — apps with HTTP/empty fetch failures
    joined_trials.parquet                # Frame.JOINED_TRIALS — final structured frame; per-(trial, app)
                                         # patent features are inlined via the join loop, not split into
                                         # a separate patents_features.parquet
```

> **v2 (deferred): `data/raw/petitions_pdf/{trial}.pdf` (~70 GB), `data/raw/petitions_pdf/_manifest.parquet`, `data/processed/petition_text/{trial}.json.gz`, `data/processed/petition_features.parquet`** — all PDF/text artifacts. Not produced in v1.

### Local vs S3 split (free-tier decision, session 2026-04-27)

| Artifact | Local | S3 | Why |
|---|---|---|---|
| `data/raw/{proceedings,documents_petition_scan,patents}/*.json` | yes (~5 GB) | yes | Idempotency cache; cheap to replicate |
| `data/processed/*.parquet` | yes (~100 MB) | yes | Feature snapshots |

**S3 mapping**: a future `clients/s3.py::S3Storage` implements the same `Storage` Protocol against `s3://<bucket>/...` — `load_object` / `save_object` / `iter_objects` / `load_frame` / `save_frame` / `*_blob` methods all map to S3 keys with no signature change. Fetchers and the joiner already accept `storage: Storage`, so swapping backends is a one-line driver change.

---

## 6. Schema boundaries

Per CLAUDE.md hard rule #5, no `dict[str, Any]` flows between stages.

| Seam | Pydantic model (in `schemas/models.py`) | Produced by | Consumed by |
|---|---|---|---|
| Proceedings raw row → trial frame | `Proceeding` *(exists)* | `ingest.fetch.fetch_proceedings` after `flatten()` | downstream |
| Document raw row (picked) → petition frame | `Petition` *(DONE)* | `parse.petition_assembler.assemble_petitions` | stage 4 join |
| Trials with no picked petition | `QuarantineEntry` *(DONE)* | same | manual review |
| Patent raw → static frame | `Patent` *(NEW)* | `ingest.fetch.fetch_patents` after `flatten()` | analysis-only |
| Patent raw + T₀ → feature row | `PatentFeatures` *(DONE)* | `parse.patent_aggregator.aggregate_patent` (canonical T₀-filter) | stage 4 join |
| Final structured frame | `JoinedTrial` *(NEW)* | `ingest.fetch.join_all` | features layer |
| *v2 dormant:* PDF fetch result | `PdfFetchManifestRow` | (would be) `clients.uspto.stream_pdf` | retry/audit |
| *v2 dormant:* Tier 0 text artifact | `PetitionTextDoc` | (would be) `parse.petition_text.extract_text` | `compute_features` |
| *v2 dormant:* Petition text features | `PetitionTextFeatures` | (would be) `parse.petition_text.compute_features` | features layer |

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

# v2 dormant — kept in schemas/models.py so v2 can light them up without churn:
# class PdfFetchManifestRow:    trial_number, bytes, sha256, http_status, fetched_at, error
# class PetitionTextDoc:        trial_number, page_count, char_count, pages, pdfplumber_version, extracted_at
# class PetitionTextFeatures:   trial_number + Tier 1/2 fields (word_count, claims_challenged,
#                               n_grounds, sotera, fintiv, …) + extracted_at
```

---

## 7. Order of operations

Each step independently runnable + verifiable.

1. ✅ **Cache + I/O primitives** — landed as `clients/local.py` (PR 1). Unit-tested in `tests/unit/test_local.py`.
2. ✅ **Pydantic seam models** — eight models added to `schemas/models.py` (PR 1).
3. ✅ **Land `config/parsers/patents.yaml`** with `proceedings`/`decisions`/`documents` surfaces. Verified via `flatten()` round-trip tests.
4. ✅ **Rewrite `ingest/fetch.py::fetch_proceedings`**. Cold-run validated ~18K rows in `trials.parquet`; cache hits on rerun.
5. ✅ **Add `parse/petition_assembler.py`** + unit tests covering picker hits, quarantine paths, T₀ cross-check.
6. ✅ **Add `ingest/fetch.py::fetch_petitions`**. Cold-run via `drivers/run_ingest_petitions.py`; verify ~17.8K petitions + ~230 quarantine; spot-check picks against the picker regression suite.
7. ✅ **Add the `patents` surface to `config/parsers/patents.yaml`** + `config/patents/event_codes.yaml`.
8. ✅ **Add `parse/patent_aggregator.py`** + unit test on the IPR2022-01002 probe payload (`patents.md`) with hand-computed expected values, including `TRIALFWD` event drop.
9. ✅ **Add `ingest/fetch.py::fetch_patents`** + `drivers/run_ingest_patents.py`. Cold-run produced `data/processed/patents.parquet` + `patent_quarantine.parquet`.
10. ✅ **Stage 4 join** — landed as `parse/joiner.py::join_all` (placed in `parse/` rather than `ingest/fetch.py` per CLAUDE.md "pagination + flatten are separate concerns" — `join_all` is pure post-processing of frames, no HTTP). Driver `drivers/run_join.py` writes `Frame.JOINED_TRIALS`. Decisions stage 1b added (`fetch_decisions` + `drivers/run_ingest_decisions.py`, ~19K IPR rows) since the `cancelled` label requires it. Cold-run: 17,303 joined rows (= 17,508 labeled − 205 petition-quarantined trials in the labeled subset).
    - **Open — FWD-outcome label resolution.** `_identify_terminating_fwd` was filtering `decisionTypeCategory` for "Final Written Decision"; empirically that field only carries "Decision"/"Rehearing Decision". Fix routed the marker to `documentTypeDescriptionText`. `terminating_outcome` now populates correctly, but `trialOutcomeCategory` comes back as `"Final Written Decision"` rather than `"All Challenged Claims Unpatentable"` on FWD rows — so the structured-field path leaves 0/5958 FWD-status trials labeled `cancelled=1` (corpus-level rate is 0.6%, all `Terminated-Adverse Judgment`).
        - **Scaffolded (2026-04-29) but not yet wired**: `config/labels.yaml::fwd_pdf_outcome` defines a regex `Determining\s+(.{2,120}?)\s+Unpatentable` over the FWD cover page (first 4K chars), with capture-word→label map (`All`→1; `No`/`Some`/`Challenged`→0). Validated against a stratified sample of 25 FWDs spanning 2021–2026 × {original, on remand, rehearing, on Remand from Director} × {parseable title, generic title}. Exposed in `schemas/constants.py` as `FWD_PDF_OUTCOME_PATTERN` / `FWD_PDF_COVER_PAGE_SEARCH_CHARS` / `FWD_PDF_CAPTURE_TO_LABEL`; bucket reserved as `Stage.DECISION_PDFS`. The same regex also matches `documentTitleText` for many FWDs (cheap-first path), but neither the FWD-PDF download driver nor the title/PDF call-site in `preprocess` is wired yet.
        - **Remaining work**: (a) FWD PDF fetcher driver writing under `Stage.DECISION_PDFS` via `storage.save_blob(..., "pdf", ...)`; (b) label-fallthrough step in `preprocess` for FWD-status trials with `terminating_outcome ∉ ALL_CLAIMS_UNPATENTABLE_OUTCOMES` — try the regex on `documentTitleText` first, then on the cover-page text from the cached PDF.

> **v2 (deferred, not part of this plan's execution)**: `clients/uspto.py::download_pdf` / `stream_pdf` → `ingest/fetch_petition_pdfs.py` (~10h cold) → `config/petitions/text_patterns.yaml` + `parse/petition_text.py` → `drivers/run_extract_petition_text.py` (Tier 1 + Tier 2 features, ~30 min CPU).

---

## 8. Open questions

Resolved in session 2026-04-27:
- ✅ **Quarantine policy**: kept in `petition_quarantine.parquet`, **excluded from joined frame** (no `in_quarantine` flag in `JoinedTrial`).
- ✅ **Static vs features parquet**: keep both. Per-app static parquet de-duplicates the patent universe; per-(trial, app) features parquet handles joinder.
- ✅ **Pagination strategy**: linear `offset` for stage 2; add `rangeFilters` partitioning only if deep-offset caps out empirically.

Resolved in session 2026-04-28 (v1 scope narrowing):
- ✅ **PDF download + text features**: deferred to v2 entirely (this includes OCR fallback, `pdfplumber` choice, PDF storage strategy, word-count-fallback policy, `n_real_parties_in_interest` parsing, and mtime-vs-hash rebuild — all questions only matter once the PDF pipeline lights up).
- ✅ **Petition scan filter**: `documentData.documentCategory IN ["PETITION", "Paper"]`. `OTHER` was probed (2026-04-28) and excluded — 0/2000 genuine petitions in OTHER, ~44% of titles contain "petition" in non-petition contexts. Cost outweighs the rare split-filing recovery.

Still open:
1. **Quarantine rescue** (~230 trials). Skip in v1; revisit only if class balance shifts or coverage matters for representativeness.

---

## 9. Critical files

v1 only:

- `src/ml_uspto/ingest/fetch.py` — stages 1–4 entrypoints
- `src/ml_uspto/parse/petition_assembler.py` — picker → frame
- `src/ml_uspto/parse/patent_aggregator.py` — canonical T₀-filter site
- `src/ml_uspto/schemas/models.py` — 8 seam models (5 active, 3 dormant for v2)
- `config/parsers/patents.yaml` — flatten configs (proceedings + decisions + documents + patents)
- `config/petition_picker.yaml` — title/blacklist/scan-filter/quarantine config
- `config/patents/event_codes.yaml` — event prefix families

v2 (deferred): `clients/uspto.py::download_pdf` / `stream_pdf`, `ingest/fetch_petition_pdfs.py`, `parse/petition_text.py`, `config/petitions/text_patterns.yaml`.
