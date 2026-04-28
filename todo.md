# todo.md

Tracking the 2026-04-27 ingestion plan (`docs/plans/2026-04-27-ingestion-pipeline.md`)
against the current working tree. Source of truth on **what each piece is for**
is the plan; this file is just a checklist.

Glossary refresher (CLAUDE.md style guide): T₀ = the day the IPR petition is
filed. "Quarantine" = trials where `pick_petition()` couldn't identify a clear
petition row; we keep them in a side parquet for audit and exclude them from
the joined frame. "Tier 0/1/2" = layers of petition-PDF features (raw text →
structural counts → substantive flags).

---

## ✅ Done (committed)

### Foundations
- [x] `schemas/` consolidation (`models.py`, `enums.py`, `constants.py`)
- [x] `clients/` package with `USPTOClient` (proceedings/decisions/documents
      POST, `download_decisions`, `get_application`, `get_documents`)
- [x] `parse/engine.py` model-agnostic flatten driven by YAML
- [x] `parse/schemas/{enums,constants}.py` subpackage pattern
- [x] `parse/petition_picker.py` (regression-tested)
- [x] `parse/preprocessing.py` label construction with three-layer resolution
      (label-1-by-status, label-0-by-status, FWD verdict)
- [x] `parse/admissibility.py` T₀ leakage filter
- [x] `models/{train,evaluate}.py` with `ModelName` enum + `ModelMetrics`
- [x] `config/labels.yaml` with empirically-validated taxonomies (commit `6ae0e12`)
- [x] `config/petition_picker.yaml` (title alts, blacklist, paper ceiling)
- [x] `config/parsers/patents.yaml` collapsed file with `proceedings` +
      `decisions` keys (commit `872509d`)
- [x] CLAUDE.md hard rules (no scattered constants, no free-string Literals,
      schemas/, clients/, structured returns, pagination separate from flatten)

### Tests already in place
- [x] `tests/unit/test_parser_engine.py`
- [x] `tests/unit/test_petition_picker.py`
- [x] `tests/unit/test_schemas.py`

### PR 1 — plumbing + models (uncommitted)
- [x] `clients/local.py` — local-FS backend, sibling-to-be of `clients/s3.py`.
      Holds both file I/O primitives (`save_parquet`, `save_json`, etc.) and
      the bucket/key/payload cache (`save`, `load_if_present`, `iter_cached`)
      with optional `root` for testability. Replaces the old `storage/`
      directory entirely; cache takes generic `bucket: str` so it stays free
      of pipeline-stage knowledge.
- [x] `ingest/schemas/enums.py` — `Stage` enum (`PROCEEDINGS`,
      `DOCUMENTS_PETITION_SCAN`, `PATENTS`); ingest-local domain vocabulary,
      passed to the cache as `bucket` (StrEnum is a str subclass).
- [x] `config/settings.yaml::api.backoff_seconds` + `APISettings` field;
      `clients/uspto.py` reads from settings instead of an inline `5 * 2**n`.
      Replaces the planned `ingest/rate_limit.py`.
- [x] 8 new Pydantic models in `schemas/models.py`: `Petition`, `QuarantineEntry`,
      `Patent`, `PatentFeatures`, `JoinedTrial`, `PdfFetchManifestRow`,
      `PetitionTextDoc`, `PetitionTextFeatures`
- [x] `tests/unit/test_local.py` — round-trip, idempotency, iter ordering
- [x] Smoke tests for each new model in `tests/unit/test_schemas.py`
- [x] CLAUDE.md updated: hard rule #5 collapses `storage/` into `clients/`;
      layout block reflects the merge.
- [x] `ml_uspto/paths.py` — central path module. Owns `PROJECT_ROOT`,
      `CREDENTIALS_ENV`, `CONFIG_DIR`, the four config-YAML constants
      (`SETTINGS_YAML`, `LABELS_YAML`, `PETITION_PICKER_YAML`,
      `PARSERS_YAML`), and settings-derived data-dir functions
      (`raw_dir()`, `processed_dir()`, `models_dir()`, `ensure_data_dirs()`).
      `settings.py`, `clients/local.py`, `parse/engine.py`,
      `parse/schemas/constants.py`, `schemas/constants.py` all route
      through paths now. Hard rule #1 in CLAUDE.md extended to filesystem
      paths.

---

## 🚧 In progress / partially landed

### `ingest/fetch.py` — needs rewrite
Currently exposes `fetch_ipr_proceedings` + `fetch_ipr_decisions` (old
prototype). Plan calls for four entrypoints: `fetch_proceedings`,
`scan_petitions`, `fetch_patents`, `join_all`. None of these exist yet.

### `config/parsers/patents.yaml` — only 2 of the planned 4 surfaces
- [x] `proceedings`
- [x] `decisions`
- [ ] `documents` — for stage 2 petition scan; **must not** include any
      `trialMetaData.*` paths (YAML enforces leakage rule statically)
- [ ] `patents` — static-only paths from `docs/api/patents.md`; **no** dated
      bag paths (those go through `patent_aggregator`)

### `features/transforms.py` — to be revisited
Old prototype that operates on the proceedings-only frame. Once stage 4
produces `trials_joined.parquet`, this file should consume `JoinedTrial` rows
(structured features + `PatentFeatures` join). Not blocking; revisit after
stage 4 lands.

---

## ⬜ Not started

### Stage 5 — petition PDF download
- [ ] `clients/uspto.py::download_pdf` and `stream_pdf` (atomic `.tmp` →
      `os.replace`, 5/10/20s 429 backoff)
- [ ] `ingest/fetch_petition_pdfs.py` driver — iterate
      `petitions.parquet`, skip if `{trial}.pdf` exists, write
      `_manifest.parquet` of `PdfFetchManifestRow`s

### Stage 6 — petition text + Tier 1/2 feature extraction
- [ ] `parse/petition_text.py` with `extract_text`, `_structural`,
      `_substantive`, `compute_features`, `build_features_parquet`
- [ ] `config/petitions/text_patterns.yaml` — every regex, header phrase,
      Sotera/Fintiv phrase, threshold (per CLAUDE.md hard rule #1)
- [ ] `parse/schemas/enums.py` — add `Parser.PETITION_TEXT`
- [ ] `parse/schemas/constants.py` — load `text_patterns.yaml`

### Ingestion plumbing
- [x] `clients/local.py` — JSON cache under `data/raw/<bucket>/<key>.json`,
      idempotency hook for every fetcher; same key shape will swap to S3
      (`clients/s3.py`)
- [x] Backoff sequence in `config/settings.yaml::api.backoff_seconds`
      (replaces the planned `ingest/rate_limit.py` — kept invariant note
      in the YAML comment instead of a code-only constant)
- [x] File I/O primitives (`save_json`, `load_json`, `save_parquet`,
      `resolve_path`) merged into `clients/local.py`; old `storage/`
      directory removed

### Parsers / aggregators
- [ ] `parse/petition_assembler.py` — group raw doc records by
      `trialNumber`, run `pick_petition()` per group, build `Petition`
      from `documentData.*` only (never `trialMetaData`)
- [ ] `parse/patent_aggregator.py` — **canonical T₀-filter site**: drop
      `eventDataBag` rows with `eventDate ≥ T₀`, drop `TRIAL*` codes,
      filter `assignmentBag`, strip `parentApplicationStatusCode`, emit
      `PatentFeatures`

### Schemas (8 new Pydantic models in `schemas/models.py`)
- [x] `Petition`
- [x] `QuarantineEntry`
- [x] `Patent` (static-only flatten)
- [x] `PatentFeatures` (T₀-aggregated, per (trial, app))
- [x] `JoinedTrial` (final structured frame)
- [x] `PdfFetchManifestRow`
- [x] `PetitionTextDoc` (Tier 0)
- [x] `PetitionTextFeatures` (Tier 1 + Tier 2)

### Config additions
- [ ] `config/patents/event_codes.yaml` — event-code prefix families
      (office_actions, ids, maintenance, trial-banned)

### Driver scripts (`scripts/` doesn't exist yet)
- [ ] `scripts/run_ingest_proceedings.py`
- [ ] `scripts/run_ingest_petitions.py`
- [ ] `scripts/run_ingest_patents.py`
- [ ] `scripts/run_join.py`
- [ ] `scripts/run_extract_petition_text.py` (chains stages 5+6 with
      `--skip-fetch`, `--skip-features`, `--text-only`)

### Test fixtures + new unit tests
- [ ] `tests/fixtures/petitions/IPR2024-XXXXX.pdf` (modern, has Sotera)
- [ ] `tests/fixtures/petitions/IPR2023-YYYYY.pdf` (modern, no Sotera)
- [ ] `tests/fixtures/petitions/IPR2014-ZZZZZ.pdf` (pre-2015 pdfplumber smoke)
- [ ] `tests/fixtures/petitions/expected_features.yaml` (locked Tier 1/2)
- [ ] `tests/unit/test_petition_assembler.py`
- [ ] `tests/unit/test_patent_aggregator.py` (incl. `TRIALFWD` event drop)
- [ ] `tests/unit/test_cache.py`
- [ ] `tests/unit/test_petition_text_structural.py`
- [ ] `tests/unit/test_petition_text_substantive.py`
- [ ] `tests/unit/test_petition_text_features.py` (round-trip)

### Cold runs
- [ ] Stage 1 cold run → `trials.parquet` (~18K rows, ~30 s)
- [ ] Stage 2 cold run → `petitions.parquet` (~17.7K) + `petition_quarantine.parquet` (~230)
- [ ] Stage 3 cold run → `patents_static.parquet` + `patents_features.parquet` (~3–4 h)
- [ ] Stage 4 join → `trials_joined.parquet`
- [ ] Stage 5 cold run → `data/raw/petitions_pdf/*.pdf` (~10 h, ~70 GB local)
- [ ] Stage 6 → `petition_text/*.json.gz` + `petition_features.parquet` (~30 min CPU)

---

## Recommended order (matches plan §7)

1. Cache + storage helpers (`ingest/cache.py`, `storage/local.py`)
2. New Pydantic models in `schemas/models.py`
3. `documents` + `patents` keys in `config/parsers/patents.yaml`
4. `config/patents/event_codes.yaml` + `config/petitions/text_patterns.yaml`
5. `download_pdf` / `stream_pdf` in `clients/uspto.py` (manual probe ~5 URIs)
6. Rewrite `ingest/fetch.py::fetch_proceedings` → cold run stage 1
7. `parse/petition_assembler.py` + tests → `scan_petitions` → cold run stage 2
8. `parse/patent_aggregator.py` + tests → `fetch_patents` → cold run stage 3
9. `join_all` → cold run stage 4 (verify row count = trials − quarantine)
10. `fetch_petition_pdfs.py` → 50-PDF slice, then full stage 5
11. `parse/petition_text.py` top-down (fixtures locked first) → stage 6

## Open questions still to resolve (plan §8)

- Word-count fallback when Certificate of Word Count is unparseable (current
  call: honest `None` over `len(re.findall(r"\w+", text))` proxy; revisit if
  `None` rate > 5%).
- `n_real_parties_in_interest` parsing — defer until 20–30 real sections seen.
- mtime vs hash rebuild for `petition_features.parquet` (mtime in v1).
- Quarantine rescue (~230 trials) — skip in v1.
