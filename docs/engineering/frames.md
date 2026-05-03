# Frames — the parquet contracts

The seven persisted `DataFrame`s that the pipeline produces. Each one has a stable name (a `Frame` enum value), a single writer, a documented set of consumers, and clear T₀ semantics.

## 1. Why `Frame`

`ml_uspto.schemas.enums.Frame` is the registry of every tabular output the project persists. One enum member ⇄ one parquet file ⇄ one storage key:

```python
class Frame(StrEnum):
    TRIALS          = "trials"          # raw proceedings, flat
    DECISIONS       = "decisions"       # raw decisions, flat
    PATENTS         = "patents"         # patent file-wrappers, flat + parallel arrays
    PETITIONS       = "petitions"       # picked petition row per trial
    PETITION_TEXTS  = "petition_texts"  # pdfplumber-extracted petition text per trial
    JOINED_TRIALS   = "joined_trials"   # the master frame (trials ⨝ decisions ⨝ petitions ⨝ patents ⨝ texts)
    FEATURES        = "features"        # the model-ready feature matrix
```

Storage methods take the enum (`storage.save_frame(df, Frame.TRIALS)`), never a raw string. The backend chooses the format (parquet for both `LocalStorage` and `S3Storage`).

Every frame on this page is keyed by some flavor of *trial number* or *application number* — those are the join surfaces.

## 2. The frames

### `Frame.TRIALS` — flat proceedings

- **Writer:** `ingest.fetch.fetch_proceedings` → `flatten(records, Parser.PROCEEDINGS)`.
- **Readers:** `ingest.run_ingest_decision_texts`, `ingest.run_ingest_patents` (just `application_number`), `parse.joiner`.
- **Grain:** one row per trial (`trial_number` unique).
- **Columns:** declared in `config/parsers/patents.yaml::proceedings.columns` — trial metadata (type, status, petition/institution/termination dates), patent owner data (patent_number, owner counsel, group_art_unit, technology_center, application_number), petitioner data (real party, counsel).
- **T₀ caveat:** `trial_status`, `institution_decision_date`, `latest_decision_date`, and `termination_date` are *post-T₀* and **must not** become features. The leakage rule (`docs/scope/prediction_scope.md` §4) applies to this frame; `features.transforms.build_features` is what enforces it. The `trial_status` column does become the *label*, not a feature.

### `Frame.DECISIONS` — flat decisions

- **Writer:** `ingest.fetch.fetch_decisions` → `flatten(records, Parser.DECISIONS)`.
- **Readers:** `parse.labels.build_labels` (label resolution), `ingest.decisions.enumerate_missing_fwd_pdfs` (gap detector), `parse.joiner`.
- **Grain:** one row per *decision document* — a trial may have several decision rows (institution decision, FWD, on-remand FWD, rehearing). FWD-original filtering happens downstream in the gap detector and the label resolver.
- **Columns:** decision metadata (`decision_issue_date`, `decision_type`, `trial_outcome`), document metadata (`document_name`, `document_title`, `document_type`, `document_filing_date`, `filing_party`), the FWD-text cache key (`document_identifier`), and the FWD PDF source URI (`file_download_uri`).
- **T₀ caveat:** every column on this frame is *post-T₀* by definition. This frame is read by the *label* path, never the feature path.

### `Frame.PATENTS` — patent file-wrappers

- **Writer:** `ingest.fetch.fetch_patents` → `parse.patents.to_flat_record` (per-app, hand-written; not flatten-engine territory — see `docs/engineering/parsers.md` §4).
- **Readers:** `parse.joiner` (left-joined onto the labeled trial frame).
- **Grain:** one row per `application_number`.
- **Columns:** scalars (filing dates, entity size, USPC class, n_inventors, n_attorneys_of_record, PTA delays) **plus parallel-array columns** (`event_codes`+`event_dates`, `assignment_received_dates`+`assignment_recorded_dates`+`assignees_per_assignment` as `list[list[str]]`, `cpc_codes`, `parent_app_numbers`).
- **T₀ caveat:** the parallel arrays carry **all** events / assignments through the patent's life — including events *after* the trial's petition filing. T₀ enforcement happens per-row in `features.patent_aggregator.aggregate_patent_row` against the joined frame's `petition_filing_date`; this frame stays raw on purpose so re-running the features stage at a different cutoff doesn't require a refetch.
- **Why `grant_date` isn't here:** it would collide with the `Frame.TRIALS` column of the same name on the joiner's left-join. The proceedings-side value wins.

### `Frame.PETITIONS` — picked petition rows

- **Writer:** `drivers/run_ingest_petitions.py` → `parse.petitions.assemble_petitions` (filter cascade; not flatten-engine territory).
- **Readers:** `ingest.petitions.enumerate_missing_petition_pdfs`, `parse.joiner`, `drivers/run_ingest_petition_text.py`.
- **Grain:** one row per trial — the picked *petition* document. Trials whose petition could not be picked don't appear here and drop out of the joined frame.
- **Columns** (from the `Petition` model in `schemas/models.py`): `trial_number`, `petition_document_id`, `petition_title`, `petition_number`, `petition_filing_date_doc`, `petition_pdf_uri`, `petition_category`.
- **Role:** scaffolding only. The petition row is identified here so the joiner can attach `petition_pdf_uri` (the handle for the petition-text ingest driver) and `petition_filing_date_doc` (the documents-side filing date used for the T₀ cross-check). The actual *features* derived from petitions land in `Frame.PETITION_TEXTS` and downstream Tier A regex extraction.

### `Frame.PETITION_TEXTS` — extracted petition text

- **Writer:** `drivers/run_ingest_petition_text.py` → `parse.petitions.build_petition_texts_frame` (walks blob cache, decodes UTF-8).
- **Readers:** `parse.joiner` (left-joined onto the joined frame; trials whose PDF hasn't been fetched/extracted carry NaN), `features.petition_text.aggregate_petition_text_row`.
- **Grain:** one row per trial that has a successfully extracted petition.
- **Columns:** `trial_number`, `petition_text` (full pdfplumber text — long strings).
- **Role:** the substrate for Tier A regex features (`n_grounds`, `n_grounds_102`, `n_grounds_103`, `has_sotera_stipulation`, `mentions_fintiv_factors` — see `docs/engineering/features/admissible_documents_analysis.md`). Stored as raw text rather than pre-extracted features so adding/revising a feature doesn't require re-OCRing.

### `Frame.JOINED_TRIALS` — the master frame

- **Writer:** `drivers/run_join.py` → `parse.joiner.join_all`.
- **Readers:** `drivers/run_features.py` (the only consumer).
- **Grain:** one row per labeled, petitioned, patent-attached trial. Pending trials drop out at the label stage; trials without a petition row drop out at the inner-join with `Frame.PETITIONS`; trials whose patent fetch failed remain (left-join) with NaN patent columns.
- **Columns:** `Frame.TRIALS` columns + `cancelled` (the binary label) + `petition_pdf_uri` + `petition_filing_date_doc` + every `Frame.PATENTS` column + `petition_text`.
- **T₀ contract:** every feature-eligible column on this frame either is a T₀-or-earlier scalar (filing dates, classifications, owner counsel) or is a raw bag the features stage filters per-row (patent parallel arrays). The columns that are *not* feature-eligible (`trial_status`, `latest_decision_date`, `termination_date`, etc.) ride along for downstream sanity-check / audit purposes — the features stage only consumes the explicitly admissible subset documented in `docs/engineering/features/`.

### `Frame.FEATURES` — model-ready matrix

- **Writer:** `drivers/run_features.py` → `features.transforms.build_features`.
- **Readers:** `models/train.py` (and any evaluation / inference code).
- **Grain:** one row per joined trial — same cardinality as `Frame.JOINED_TRIALS` minus rows dropped by `select_usable_rows` (petitions whose extracted text is too short to support Tier A features).
- **Columns:** numeric and one-hot-encoded columns plus the binary `cancelled` target. Specific column list is documented in `docs/engineering/features/features_csv_dictionary.md`.
- **T₀ contract:** **enforced**. Every column on this frame is either observable at T₀ or has been row-locally truncated to `≤ petition_filing_date` (patent aggregator) or extracted from a T₀-frozen artifact (the petition text itself). The leakage rule is the build's invariant.

## 3. T₀ semantics summary

| Frame | T₀ status | Notes |
|---|---|---|
| `TRIALS` | mixed | Most cols are T₀-or-earlier; `trial_status` and post-institution dates are post-T₀. Used for *labels*, not features (except admissible scalars). |
| `DECISIONS` | post-T₀ | Label-only. Never read by features. |
| `PATENTS` | mixed (raw) | Parallel arrays span the patent's full life; T₀ truncation is per-row downstream. |
| `PETITIONS` | T₀ | Picked at petition-filing time. |
| `PETITION_TEXTS` | T₀ | The petition is filed *at* T₀ — the text is the canonical T₀-frozen artifact. |
| `JOINED_TRIALS` | mixed | The label is attached; feature-eligible vs ride-along columns are documented per-feature. |
| `FEATURES` | T₀ | Invariant — leakage discipline lives here. |

## 4. Adding a new frame

1. Add a `Frame` member with the storage-key value.
2. Identify the writer (likely a driver under `drivers/`) and have it call `storage.save_frame(df, Frame.NEW)`.
3. Document the row above (writer, readers, grain, columns, T₀ status).
4. If it's a feature surface, add the feature-catalog row in `docs/engineering/features/features_csv_dictionary.md`.

## Cross-references

- `docs/engineering/storage.md` — the `save_frame`/`load_frame` Protocol.
- `docs/engineering/parsers.md` — how raw API JSON becomes the *raw* frames.
- `docs/engineering/features/` — what `Frame.FEATURES` columns mean.
- `docs/scope/prediction_scope.md` §4 — the T₀ leakage rule in domain terms.
