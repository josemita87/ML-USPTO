# Ingestion Pipeline — Systems Plan

**Date**: 2026-04-27 (v1 scope narrowed 2026-04-28)
**Status**: stages 1–3 landed (proceedings, petitions, patents cold-runs validated); stage 4 (join) is the only v1 step remaining.
**Scope (v1)**: end-to-end ingestion from "no local data" to a feature-ready parquet keyed by `trialNumber`, joining proceedings + petition pointer + patent. **Metadata-only.** PDF download and petition-text feature extraction (the original stages 5–6) are deferred to v2 — see `### v1 cut` below.

The original plan covered six stages. v1 stops at stage 4 (the join). This document still describes the 4-stage metadata pipeline; deleted text covers what v1 deliberately omits.

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
      enums.py                        [DONE — Stage enum (proceedings, documents_petition_scan, patents)]
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
  run_ingest_petitions.py             [DONE]
  run_ingest_patents.py               [NEW]
  run_join.py                         [NEW]

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
def fetch_proceedings(client: USPTOClient, *, page_size: int) -> pd.DataFrame
def fetch_petitions(client: USPTOClient, *, page_size: int) -> Iterator[dict]
def fetch_patents(client: USPTOClient, trials: pd.DataFrame) -> pd.DataFrame
def join_all(trials: pd.DataFrame, petitions: pd.DataFrame, patents: pd.DataFrame) -> pd.DataFrame
```

Each paginates → caches → flattens via `parse.engine.flatten` → validates row-by-row into a Pydantic model → writes parquet. `fetch_petitions` is a generator; `parse.petition_assembler.assemble_petitions` consumes it. `join_all` left-joins trials ⟕ petitions ⟕ patents on `(trial_number, application_number)`, validates each row into `JoinedTrial`, **drops quarantined trials** (per session decision 2026-04-27).

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

v1 drivers: `run_ingest_proceedings.py` *(DONE)*, `run_ingest_petitions.py` *(DONE)*, `run_ingest_patents.py` *(NEW)*, `run_join.py` *(NEW)*.

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
    trials.parquet                       # one row per trial, with label
    petitions.parquet                    # one row per trial-with-petition
    petition_quarantine.parquet          # ~230 rows; review manually
    patents_static.parquet               # one row per application
    patents_features.parquet             # one row per (trial, app), T₀-filtered
    trials_joined.parquet                # final structured frame (excludes quarantine)
```

> **v2 (deferred): `data/raw/petitions_pdf/{trial}.pdf` (~70 GB), `data/raw/petitions_pdf/_manifest.parquet`, `data/processed/petition_text/{trial}.json.gz`, `data/processed/petition_features.parquet`** — all PDF/text artifacts. Not produced in v1.

### Local vs S3 split (free-tier decision, session 2026-04-27)

| Artifact | Local | S3 | Why |
|---|---|---|---|
| `data/raw/{proceedings,documents_petition_scan,patents}/*.json` | yes (~5 GB) | yes | Idempotency cache; cheap to replicate |
| `data/processed/*.parquet` | yes (~100 MB) | yes | Feature snapshots |

**S3 mapping**: `s3://<bucket>/raw/<key>.json` is a 1:1 swap of cache backend. `clients/local.py` and a future `clients/s3.py` expose the same `cache_path` / `load_if_present` / `save` / `iter_cached` shape; the fetcher gets a backend flag and stays oblivious to which one it's hitting.

---

## 6. Schema boundaries

Per CLAUDE.md hard rule #5, no `dict[str, Any]` flows between stages.

| Seam | Pydantic model (in `schemas/models.py`) | Produced by | Consumed by |
|---|---|---|---|
| Proceedings raw row → trial frame | `Proceeding` *(exists)* | `ingest.fetch.fetch_proceedings` after `flatten()` | downstream |
| Document raw row (picked) → petition frame | `Petition` *(DONE)* | `parse.petition_assembler.assemble_petitions` | stage 4 join |
| Trials with no picked petition | `QuarantineEntry` *(DONE)* | same | manual review |
| Patent raw → static frame | `Patent` *(NEW)* | `ingest.fetch.fetch_patents` after `flatten()` | analysis-only |
| Patent raw + T₀ → feature row | `PatentFeatures` *(NEW)* | `parse.patent_aggregator.aggregate` (canonical T₀-filter) | stage 4 join |
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
10. **Add `ingest/fetch.py::join_all`** + `drivers/run_join.py`. Verify final parquet row count = `len(trials) - len(quarantine)`.

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
