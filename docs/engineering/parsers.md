# Parsers — flatten engine + per-surface parsers

How raw USPTO ODP JSON becomes typed tabular rows. Two tiers: a YAML-driven flatten engine for simple surfaces, hand-written parsers for the surfaces with shapes the engine can't express.

## 1. Why two tiers

Most USPTO PTAB API surfaces are flat-ish — every record has scalar fields under known dotted paths (`trialMetaData.petitionFilingDate`, `documentData.fileDownloadURI`). For those, a column mapping in YAML plus a generic projector is enough.

A few surfaces aren't:
- **Patents** (`/applications/{appNum}`) — every row carries parallel-array columns (`event_codes` aligned with `event_dates`, `assignees_per_assignment` as `list[list[str]]` to preserve which assignees belong to which assignment). YAML's flat `output_col: dotted.path` model can't express that.
- **Petitions** (`/documents/search`) — picking the petition row from a trial's filing list is a *filter cascade* (drop exhibits, drop high paper numbers, title regex, blacklist, lowest documentNumber wins), not a projection. Pre-2022 trials lump petitions into the legacy `Paper` category alongside motions and orders, so the picker is title-driven, not category-driven.

So the rule is:
- Scalar projection from a known dotted path → **YAML + the engine** (`config/parsers/patents.yaml` + `parse/flatten.py`).
- Anything else (parallel arrays, cross-record picking, multi-step normalization) → **a hand-written parser** under `parse/<surface>.py`.

## 2. The YAML schema

`config/parsers/patents.yaml` has one top-level key per declarative surface:

```yaml
proceedings:
  columns:
    trial_number: trialNumber
    trial_type: trialMetaData.trialTypeCode
    petition_filing_date: trialMetaData.petitionFilingDate
    …

decisions:
  columns:
    trial_number: trialNumber
    decision_issue_date: decisionData.decisionIssueDate
    document_identifier: documentData.documentIdentifier
    file_download_uri: documentData.fileDownloadURI
    …
```

The top-level keys correspond exactly to `parse.schemas.enums.Parser` members (`PROCEEDINGS`, `DECISIONS`). Any column not in `columns` simply doesn't appear in the resulting parquet — the engine is a pure projection, no implicit fields.

Output column order matches YAML declaration order (Python 3.7+ ordered dicts). Use that to keep parquet files diff-friendly.

## 3. The engine

`parse/flatten.py` is 60 lines. Three functions:

```python
def load_parser_config(parser: Parser) -> dict[str, Any]: ...
def flatten_records(records, config) -> pd.DataFrame: ...
def flatten(records, parser: Parser) -> pd.DataFrame: ...   # convenience wrapper
```

`flatten_records` walks each record once, calling a tiny `_get_path(obj, "a.b.c")` helper that returns `None` whenever any segment is missing or non-mapping. **Missing fields surface as None in the resulting DataFrame, not as KeyErrors** — the API is permissive about which fields populate per record (e.g., `terminationDate` is null for active trials), and we want that to flow through to parquet.

The YAML is `lru_cache`'d on first load. Hot reload: clear `_load_all.cache_clear()` if you mutate the file in a notebook.

Public usage from `ingest/fetch.py`:

```python
df = flatten(records, Parser.PROCEEDINGS)
storage.save_frame(df, Frame.TRIALS)
```

That's it. No per-surface flatten function under `ingest/`, no `_flatten_decisions(...)` helpers.

## 4. The hand-written parsers

Two surfaces need imperative code:

### `parse/patents.py` — patent file-wrappers

`to_flat_record(payload)` turns one `/applications/{appNum}` response into a flat dict with:
- **Scalars** read off `applicationMetaData.*` (filing dates, entity size, USPC class, …) and `patentTermAdjustmentData.*` (PTA delays).
- **Parallel-array columns** built off `PatentFileWrapper.events` / `.assignments`:
  - `event_codes: list[str]` aligned by index with `event_dates: list[date]`.
  - `assignment_received_dates` / `assignment_recorded_dates` aligned with `assignees_per_assignment: list[list[str]]` (preserved per-assignment grouping).

`PatentFileWrapper` (in `schemas/models.py`) is the typed validation contract — `parse_patent_wrapper` constructs it; `to_flat_record` reads off it. The wrapper handles two USPTO API quirks:
- The `patentFileWrapperDataBag` envelope (sometimes records are bare, sometimes wrapped in a length-1 list).
- `cpcClassificationBag` entries that are inconsistently typed across the corpus (sometimes `list[str]`, sometimes `list[dict]`, sometimes a single non-list value) — `_normalize_cpc_classifications` flattens all variants before validation.

T₀ leakage discipline (filtering events to `≤ petition_filing_date`) doesn't happen here — patent rows are *raw* in `Frame.PATENTS`. The features stage applies the cutoff per-row in `features/patent_aggregator.py`.

### `parse/petitions.py` — petition picking

`pick_petition(rows)` runs the filter cascade described above against a trial's full document list and returns the chosen row. `assemble_petitions(storage)` wraps it: walks `Stage.DOCUMENTS_PETITION_SCAN`, picks per trial, returns a `Frame.PETITIONS`-shaped DataFrame of `Petition` rows.

## 5. Adding a new API surface

If the new surface is flat (scalar dotted-path projections only):

1. Add a top-level key to `config/parsers/patents.yaml` mirroring the existing surfaces.
2. Add a member to `parse/schemas/enums.py::Parser` whose `.value` matches the YAML key.
3. Add a fetcher in `ingest/fetch.py` that calls `flatten(records, Parser.<NAME>)` and `save_frame(df, Frame.<NAME>)`.
4. Add a `Frame` member if the output deserves its own parquet.

If it's not flat (parallel arrays / cross-record logic):

1. Skip the YAML — write a `parse/<surface>.py` module exposing a `to_flat_record` (per-record) or `assemble_*` (per-corpus) function.
2. If validation is non-trivial, add a typed wrapper to `schemas/models.py` (cf. `PatentFileWrapper`).
3. Wire it up from `ingest/fetch.py` the same way.

## 6. Don't bake parsing into fetchers

`ingest/fetch.py::_paginate_cached` paginates and yields raw records; flattening is one-line downstream (`flatten(records, Parser.X)`). Keep these concerns separate — fetchers cache pages, parsers project records. Mixing them broke twice during early development (decisions had its own raw-cache walker that mirrored what the engine already did from parquet); both are gone.

## Cross-references

- `docs/engineering/frames.md` — what the flattened frames carry, who reads them.
- `docs/engineering/pipeline.md` — where parsers fit in the ingest DAG.
- `docs/api/proceedings.md` / `docs/api/patents.md` — what each upstream surface returns.
