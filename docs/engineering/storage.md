# Storage backends

How the project persists and retrieves data. Two backends share one Protocol; pipeline code never knows which one it's talking to.

## 1. The contract

`ml_uspto.protocols.storage.Storage` is the only surface anything in `parse/`, `ingest/`, `features/`, or `models/` ever calls. It exposes three resource families:

| Family | Methods | Keyed by | On-disk shape |
|---|---|---|---|
| **Frames** | `load_frame` / `save_frame` | `Frame` enum (no bucket — single namespace) | parquet |
| **JSON objects** | `load_object` / `save_object` / `iter_objects` | `(bucket, key)` — bucket is a `Stage` value, key is opaque (page number, application number, …) | one `.json` file per object |
| **Binary blobs** | `load_blob` / `save_blob` / `has_blob` / `iter_blob_keys` | `(bucket, key, ext)` | one `.<ext>` file per blob |

Why three families:
- **Frames** are the project's *processed* outputs — typed parquet tables, one source-of-truth file per `Frame` member. Catalog: see `docs/engineering/frames.md`.
- **JSON objects** are the *raw* API cache — paginated search payloads (`page_NNNN.json`) and per-application file-wrapper records (`<appNum>.json`). Caching the full payload (not just the records) lets `_paginate_cached` resume mid-stream because the `count` field survives.
- **Blobs** are PDFs and pdfplumber-extracted text — opaque bytes that don't fit either of the above.

Callers depend on the Protocol, not on a concrete class. Drivers and orchestrators go through `get_storage()`; tests instantiate `LocalStorage(raw_root=tmp_path/"raw", processed_root=tmp_path/"processed")` directly.

## 2. `LocalStorage` (default)

Two filesystem roots:

```
data/raw/                                  # raw_root
  proceedings/page_0000.json               # JSON objects, bucket=Stage.PROCEEDINGS
  decisions/page_0000.json
  patents/14123456.json
  decision_texts/<doc_id>.txt              # blobs (extracted text)
  decision_texts/<doc_id>.pdf              # blobs (raw PDF)
  petition_texts/<trial>.pdf
data/processed/                            # processed_root
  trials.parquet                           # one file per Frame member
  decisions.parquet
  …
```

Both roots default to `paths.raw_dir()` / `paths.processed_dir()` (settings-derived, see `docs/engineering/configuration.md`). Tests override them with `tmp_path`.

The split mirrors a meaningful boundary — *raw* (cache, regeneratable from API) vs *processed* (project-derived, our outputs). Wiping `data/raw/` + rerunning is the disaster-recovery path; wiping `data/processed/` is cheap (rerun ingest).

## 3. `S3Storage`

Same Protocol, single bucket. The two local roots collapse into two key prefixes (`raw/`, `processed/` by default), so keys are byte-identical to local paths:

```
s3://<bucket>/raw/proceedings/page_0000.json
s3://<bucket>/processed/trials.parquet
```

This is intentional: **`aws s3 sync data/raw/ s3://<bucket>/raw/` is a valid bootstrap seed**. You can develop against the local cache, then promote everything to S3 without renaming a single key.

Implementation notes:
- Round-trips parquet through `BytesIO` rather than `s3fs` to keep the dependency surface to plain `boto3`.
- `iter_objects` paginates via `list_objects_v2` and re-fetches each object body; not free for large buckets, but only `parse/labels.py`'s cached-text fallback uses it.
- 404s are caught against `S3_NOT_FOUND_CODES` (`NoSuchKey`, `404`) and surface as `None` from `load_*` / `False` from `has_blob`.

## 4. Backend selection

`get_storage()` (in `clients/storage/__init__.py`) reads `settings.storage.backend` and dispatches:

```python
ML_USPTO_STORAGE=local   → LocalStorage()                 # default
ML_USPTO_STORAGE=s3      → S3Storage(bucket=ML_USPTO_S3_BUCKET)
```

The Fargate task definition injects `ML_USPTO_STORAGE=s3` + the bucket name; laptop runs default to `local` with no env vars set. **No application code reads `os.environ` directly** — the typed `StorageSettings` is the only entry point.

## 5. Shared serialization

`_serialization.json_default` is the `default=` hook both backends pass to `json.dumps`. It handles two non-JSON-native types:
- `datetime.date` / `datetime.datetime` → ISO string (label rows, decision dates).
- `pathlib.Path` → string (manifest audit rows).

Centralizing the hook is what guarantees **byte-identical JSON across backends** — drift between local and S3 would silently break `aws s3 sync` round-trips. `TypeError` for anything else; we don't want a quiet `repr()` fallback.

## 6. When to extend

- **New JSON shape** (e.g. an audit log) → just call `save_object(bucket, key, payload)` with a fresh bucket name. No backend changes needed; the Protocol is open over `bucket: str`.
- **New binary blob type** (e.g. extracted images) → same: `save_blob(bucket, key, ext, bytes_)`.
- **New tabular output** → add a `Frame` member, call `save_frame(df, Frame.NEW)`. Both backends pick it up automatically.
- **New backend** (GCS, Azure Blob, an in-memory test fake) → implement the Protocol; both `LocalStorage` and `S3Storage` are reference impls totaling ~250 lines.

## Cross-references

- `docs/engineering/pipeline.md` — DAG that drives reads/writes against this layer.
- `docs/engineering/frames.md` — what each persisted frame contains.
- `docs/engineering/configuration.md` — settings + env vars that pick the backend.
