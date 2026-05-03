# Configuration — settings, env, and paths

How runtime knobs (API key, page size, storage backend, data dirs) flow into the application. The hard rule: **no application code reads `os.environ` directly.** Every env-driven value lands as a typed field on a `BaseSettings` subclass and is accessed via `get_settings()`.

## 1. The three layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Code defaults (in src/ml_uspto/settings.py)                     │  ← lowest priority
│   ↓                                                              │
│  config/settings.yaml                                            │  ← fills sub-settings on construction
│   ↓                                                              │
│  Environment variables (.credentials.env + process env)          │  ← highest priority
└─────────────────────────────────────────────────────────────────┘
                              ↓
                       get_settings()  → cached Settings instance
```

YAML provides shipped defaults (page sizes, retry sleeps, data dir layout). Env vars override anything that needs to differ per environment (the API key, which storage backend to use, the S3 bucket name). Pydantic's resolution order is what makes env take precedence.

## 2. The settings tree

`src/ml_uspto/settings.py` exposes one `Settings` object composed of five sub-settings:

```
Settings
├── api      : APISettings       # USPTO ODP client knobs
├── data     : DataSettings      # data dir locations
├── storage  : StorageSettings   # storage backend selector
├── features : FeatureSettings   # feature-engineering toggles
└── model    : ModelSettings     # train/eval knobs
```

Access pattern from anywhere in the codebase:

```python
from ml_uspto.settings import get_settings
s = get_settings()              # cached, lru_cache(maxsize=1)
s.api.page_size                 # 100
s.storage.backend               # "local" | "s3"
s.data.raw_dir                  # PosixPath('data/raw')
```

Two of the five sub-settings read from env — the others are pure YAML/code:

| Sub-settings | Env prefix | Source order |
|---|---|---|
| `APISettings` | `USPTO_` | `.credentials.env` → process env → YAML defaults |
| `StorageSettings` | `ML_USPTO_` | `.credentials.env` → process env → code defaults |
| `DataSettings` | (none) | YAML only |
| `FeatureSettings` | (none) | YAML only |
| `ModelSettings` | (none) | YAML only |

### Env-var inventory

Two prefixes, four variables:

| Var | Prefix | What it does | Default |
|---|---|---|---|
| `USPTO_API_KEY` | `USPTO_` | USPTO ODP API key for `api.uspto.gov` | `""` (anonymous; rate-limited) |
| `USPTO_BASE_URL` | `USPTO_` | API root override (rarely needed) | `https://api.uspto.gov/api/v1/patent` |
| `ML_USPTO_STORAGE` | `ML_USPTO_` | `local` or `s3` — picks the backend in `clients/storage/__init__.py::get_storage` | `local` |
| `ML_USPTO_S3_BUCKET` | `ML_USPTO_` | Bucket name when `STORAGE=s3` | `""` (raises if `STORAGE=s3` and unset) |

The Fargate task definition injects `ML_USPTO_STORAGE=s3` + `ML_USPTO_S3_BUCKET=<...>`; laptop development runs default to local with no env vars set.

The convention: **`USPTO_` is for things that talk to the USPTO API; `ML_USPTO_` is for things that configure how this project runs.** A new env var picks its prefix by that test.

### `.credentials.env`

Lives at `<project_root>/.credentials.env` (path resolved by `paths.CREDENTIALS_ENV`). Pydantic-settings reads it via `SettingsConfigDict(env_file=paths.CREDENTIALS_ENV)` for both `APISettings` and `StorageSettings`. Format is dotenv:

```
USPTO_API_KEY=...
ML_USPTO_STORAGE=local
```

Process env wins over the file when both set the same var (standard dotenv behavior). The file is gitignored; the repository ships `.env.example` (or equivalent) as the template.

## 3. The hard rule

**Never `import os` to read configuration.** Every env-driven value has a row on `APISettings` or `StorageSettings`. If you find yourself wanting `os.environ["NEW_THING"]`:

1. Add a typed field to the appropriate sub-settings class.
2. Pick a documented prefix (`USPTO_` or `ML_USPTO_`).
3. Document the new var in this doc's inventory table.
4. Read it via `get_settings().section.field`.

Why: env-var names, defaults, and validation belong in one place. Drift between "what we read" and "what we document" is what makes deployment configuration silently break across environments. The factory layer (`get_storage`) is the only acceptable consumer of `StorageSettings`; everything else flows through the typed Protocol-typed clients.

## 4. `paths.py` — the filesystem source of truth

`src/ml_uspto/paths.py` is the single source of truth for filesystem locations. Every absolute path goes through it; **no code outside `paths.py` writes `PROJECT_ROOT / "..."` inline.**

What it exposes:

```python
PROJECT_ROOT             # absolute path to repo root
CREDENTIALS_ENV          # PROJECT_ROOT / ".credentials.env"
CONFIG_DIR               # PROJECT_ROOT / "config"

# Named YAML files
SETTINGS_YAML            # config/settings.yaml
LABELS_YAML              # config/labels.yaml
PETITION_PICKER_YAML     # config/petition_picker.yaml
PARSERS_YAML             # config/parsers/patents.yaml
PATENT_EVENT_CODES_YAML  # config/patents/event_codes.yaml

# Data dirs (settings-derived; lazy)
raw_dir()                # absolute path to data/raw  — used by LocalStorage
processed_dir()          # absolute path to data/processed
models_dir()             # absolute path to data/models

# Helpers
resolve(rel)             # resolve a project-relative path to absolute
ensure_data_dirs()       # mkdir -p for the three data dirs
```

The data-dir functions defer the `get_settings()` import to runtime (avoids the circular `paths ↔ settings` import) and are settings-derived — they ultimately read from `DataSettings.raw_dir` etc., so a YAML change to `data.raw_dir` propagates without touching code.

## 5. The YAML config inventory

Every YAML file in the repo has one canonical home + one Python loader:

| File | Loader | What's in it |
|---|---|---|
| `config/settings.yaml` | `settings.py::_load_yaml` | Defaults for the `Settings` tree (api / data / features / model) |
| `config/labels.yaml` | `schemas.constants` (via `_load_yaml`) | Label-construction taxonomies (statuses, decision titles) |
| `config/petition_picker.yaml` | `parse.schemas.constants` | Title regex alternatives, blacklist, paper-number ceiling |
| `config/parsers/patents.yaml` | `parse.flatten._load_all` | Per-API-surface column mappings — see `docs/engineering/parsers.md` |
| `config/patents/event_codes.yaml` | `features.schemas.constants` | Patent file-wrapper event-code → category mappings |

Adding a new YAML config: put the file under `config/`, add a path constant to `paths.py`, expose loaded values through the appropriate `<package>/schemas/constants.py`. **No module-level `with open(...)`** in application code — paths go through `paths`, loading goes through `schemas/constants.py`.

The Hard Rule from `CLAUDE.md` in one sentence: domain-revisable values (taxonomies, regexes over legal text, thresholds a non-engineer might tune) live in YAML and are exposed through a `schemas/constants.py` import site. Genuinely internal mechanical constants (API response-shape mappings, retry counts) live as Python literals in `schemas/constants.py` directly.

## 6. Tests

Tests don't touch `get_settings()` for storage — they instantiate `LocalStorage(raw_root=tmp_path/"raw", processed_root=tmp_path/"processed")` directly to isolate filesystem I/O per test. The `lru_cache` on `get_settings` is safe: it's read inside `get_storage()` (not invoked by tests that pass storage in directly) and inside `paths.raw_dir()` / `paths.processed_dir()` (which a test bypasses by passing the roots explicitly).

If a test ever needs to override settings, the right pattern is `get_settings.cache_clear()` + monkeypatching env vars before the call — not mutating the cached instance.

## Cross-references

- `docs/engineering/storage.md` — how `StorageSettings` selects the backend.
- `docs/engineering/parsers.md` — `PARSERS_YAML` consumer.
- `docs/api/rate_limits.md` — what the `api.*` settings actually budget against.
