# API ↔ Feature Map

A first-pass cross-reference between the USPTO PTAB endpoints we have wired up and the feature families we care about for trial-outcome prediction. Use this as a quick map when designing feature extraction: *for feature X, which endpoint do I hit?*

Client code: `src/data/client.py`. Normalized schemas: `exploration/proceedings.md`.

**Authoritative API references**:
- Swagger UI / OpenAPI spec: <https://data.uspto.gov/swagger/index.html> — endpoint paths, request parameters, response body schemas.
- Query syntax reference: `ODP-API-Query-Spec.pdf` (in this directory).
- Empirical-probing note: per `feedback_empirical_over_spec` memory, prefer hitting endpoints with the live client to confirm real response shape before coding to the spec — Swagger examples include synthetic placeholder data.

---

## 1. Endpoints available

| Endpoint | Method in `USPTOClient` | Returns | Primary use |
|---|---|---|---|
| `GET /trials/proceedings/search` | `search_proceedings()` | Paginated list of proceedings (trial metadata) | Simple keyword search |
| `POST /trials/proceedings/search` | `search_proceedings_post()` | Same as GET but accepts structured `filters`, `rangeFilters`, `fields`, `facets`, `sort` | **Primary ingestion path** — target-variable source, surgical date/type slicing, facet exploration |
| `GET /trials/proceedings/search/download` | *(not wrapped)* | CSV or JSON file attachment of proceeding-level metadata (one row per case) | Bulk metadata export when you want all ~19K proceedings as a single file instead of paginating `/search`. **Empirically returns 403 'Forbidden' under load** while `/search` and path-style endpoints continue to work — see download-bucket caveat below. |
| `GET /trials/proceedings/{trial_number}` | `get_proceeding()` | Single proceeding's full record. Includes `trialMetaData.fileDownloadURI` → **whole-case ZIP of every filing's PDF**. | Detailed per-case metadata lookup; also the entry point for whole-docket bulk pulls. |
| `GET /trials/decisions/search` | `search_decisions()` | Paginated list of decisions | Simple keyword search |
| `POST /trials/decisions/search` | `search_decisions_post()` | Structured decision metadata + a **500-char OCR preview** (`documentData.documentOCRText`). Full decision text is **not** returned. | **Primary path for structured decision signals** — `decisionData.statuteAndRuleBag`, `issueTypeBag`, `trialOutcomeCategory`, `appealOutcomeCategory`. For full text (Fintiv factor ratings, dispositive factor) you must fetch the decision PDF via `fileDownloadURI`. |
| `POST /trials/decisions/search/download` | `download_decisions()` | CSV or JSON file attachment of decision metadata | Fast tabular export; **restricted field projection** — rejects `documentOCRText`, `fileDownloadURI`, `appealOutcomeCategory` |
| `GET /trials/{trial_number}/documents` | `get_trial_documents()` | Full filing list for a trial (petition, POPR, institution decision, briefs, FWD, …) | Per-trial document index — still needed for petition / POPR text (which is *not* in the decisions endpoint response) |
| `GET /trials/documents/search/download` | *(not wrapped, inferred — not yet probed)* | Cross-trial filing-level metadata as CSV/JSON. One row per filing with `documentTypeName`, `documentFilingDate`, `fileDownloadURI`. | Bulk filing-index for "find all institution decisions / all petitions across the whole corpus" without per-trial calls. **Schema not yet empirically verified.** |
| `GET <trialMetaData.fileDownloadURI>` | *(not wrapped)* | Whole-case ZIP of every filing's PDF. Probed on IPR2026-00339: 33 PDFs / 202 MB. | Per-proceeding bulk PDF pull. Use targeted single-PDF fetches (via `documentData.fileDownloadURI`) over this when you only need decision PDFs — naive whole-case zips × 18K cases ≈ multi-TB. |

All `/trials/*` calls above fall in the **metadata retrieval bucket** (5M calls/week combined, serial-only, burst=1). See `rate_limits.md` for full constraints.

> ⚠ **`/search/download` endpoints sit in a stricter bucket than `/search`.** Empirically observed 2026-04-25: same key, same IP, same minute — `/proceedings/search/download?q=IPR2026-00339` returned `{"message":"Forbidden"}` (HTTP 403, AWS API Gateway-style body) while `/proceedings/search` and `/proceedings/{trial_number}` returned 200 fine. Swagger UI calls also kept working. Hypothesis: download endpoints are gated by a separate WAF/quota rule (possibly on caller fingerprint — Swagger sends `Origin: data.uspto.gov`). For ingestion at scale, prefer the paginated POST `/search` route over `/search/download` until this is diagnosed; or front the call with `Origin`/`Referer`/browser-`User-Agent` headers.

### The three-level data model

PTAB data is structured as a one-to-many hierarchy. Every download endpoint exports metadata at one of these levels — choosing the right one is about granularity, not filtering:

```
Proceeding (a trial / case)              ~19K records      → /proceedings/search[/download]
   └── Documents (filings within a case) ~millions         → /documents/search/download, /trials/{n}/documents
          └── PDF bytes                  the actual files  → fetch <fileDownloadURI>
```

**Same trial number filter, three different return shapes:**

| Call | Rows for IPR2026-00339 | Each row represents |
|---|---:|---|
| `/proceedings/search/download?q=IPR2026-00339` | 1 | the case (header) |
| `/documents/search/download?q=IPR2026-00339` *(inferred)* | ~33 | one filing in the case |
| `/decisions/search/download?q=IPR2026-00339` | 0–N | one decision-type filing |
| `GET <trialMetaData.fileDownloadURI>` | (binary) | every PDF in the case, zipped |

Filtering changes *how many* rows match; granularity determines *what each row is*. The endpoints are **complementary tables joined on `trialNumber`**, not interchangeable views — pick by what each row should mean.

### Two distinct `fileDownloadURI` fields

The same field name appears at two granularities and means different things — easy to confuse:

| Source | What it points to | Size |
|---|---|---|
| `trialMetaData.fileDownloadURI` (on a proceeding record) | **Whole-case ZIP** of every filing's PDF | ~50–500 MB per case (probed: 202 MB / 33 PDFs for IPR2026-00339) |
| `documentData.fileDownloadURI` (on a document or decision record) | **Single PDF** of one specific filing | ~0.5–25 MB per file |

Both are direct downloads via the authenticated session (`X-API-Key`). Reuse `client.session.get(uri)` rather than building a fresh request. The whole-case zip is *not* what `/search/download` returns — that endpoint exports search-result metadata as a CSV/JSON file and contains zero PDF content.

> ⚠ **`documentOCRText` is a 500-char preview, not the full text.** Confirmed empirically 2026-04-24: every endpoint that returns `documentOCRText` (decisions search, documents endpoint, per-document detail endpoints) caps it at 500 characters — enough for the case caption and judge names, nothing substantive. For full decision text you must fetch the PDF via `documentData.fileDownloadURI`. This is a global API behavior, not a `fields`-projection artifact.

> ⚠ **Naming collision:** the ODP catalog also exposes `/api/v1/petition/decisions/*` ("Petition Decision Search"). This is **not** PTAB data — it is the USPTO Office of Petitions' rulings on procedural prosecution matters (PPH requests, PTA disputes, abandonment revivals). Wrong corpus for this project. Telltale: `finalDecidingOfficeName: "OFFICE OF PETITIONS"` in the record. See `ptab_scope_and_terminology.md` §3.

### Decisions vs. documents — overlap, not duplication

The decisions and documents endpoints return **the same physical papers** for the decision-type subset. Confirmed empirically on `IPR2022-01002`: the 3 records from `/trials/decisions/search` have identical `documentIdentifier`s to 3 of the 145 records from `/trials/{trial}/documents`. Same PDFs, same OCR text.

| | `/trials/{trial}/documents` | `/trials/decisions/search` |
|---|---|---|
| Scope | Per-trial, **all papers** (petitions, POPRs, exhibits, orders, decisions, …) | Cross-trial, **decision papers only** |
| Query | GET, `{trial}` in path | POST with full Simplified Query Syntax |
| Extra fields | None — documents only | **`decisionData.{trialOutcomeCategory, decisionTypeCategory, issueTypeBag, statuteAndRuleBag, appealOutcomeCategory}`** |
| OCR reliability | Inconsistent (observed 3/25 populated); **500-char preview** when present | Reliable when requested via `fields`, but **capped at 500 chars** (preview, not full text) |

Conceptually the decisions endpoint is a filtered+enriched view over the decision-type subset of documents. To avoid duplicate ingestion, pick a path per use case rather than calling both for the same trial:

- **Decision text + outcome labels (cross-trial)** → decisions POST only.
- **Petitions / POPRs / exhibits / full timeline (per-trial)** → documents endpoint; strip or skip decision-type papers if already ingested.
- **Both at scale** → decisions POST first (cross-trial), then per-trial documents calls filtered to `trialDocumentCategory != "Decision"`.

### POST Simplified Query Syntax — key notes

- Field names use **dotted paths**: `trialMetaData.trialTypeCode`, `patentOwnerData.patentNumber`, `decisionData.issueTypeBag`.
- `filters` (exact-match list) and `rangeFilters` (date/number inclusive ranges) are the primary slicing tools. Example: `trialMetaData.trialTypeCode = IPR` + `petitionFilingDate ∈ [2023-01-01, 2023-03-31]`.
- `fields` trims the response payload; wildcards and parent fields (implies all children) are supported. Invalid field names are **silently ignored**, so verify by reading the response.
- `facets` returns aggregated counts — used for cheap distribution sanity checks without iterating records.
- Full syntax reference: `ODP-API-Query-Spec.pdf`.

### Observed counts (probed 2026-04-24)

- 19,246 proceedings total: **IPR 18,058, CBM 602, PGR 558, DER 28**.
- Proceeding status distribution (the label space): *Institution Denied* 6,015; *Final Written Decision* 5,838; *Terminated-Settled* 4,408; *Terminated* 933; *Discretionary Denial* 661; *FWD – Appealed* 591; *Trial Instituted* 351; others small.

---

## 2. Feature → Endpoint map

Feature families follow the priority order in the top-level `README.md` §6.

| # | Feature family | Specific feature | Endpoint | Source field / derivation | Notes |
|---|---|---|---|---|---|
| — | **Target** | Trial outcome | proceedings POST | `trialMetaData.trialStatusCategory` | Label — not a feature |
| — | **Target** | Decision-level outcome | decisions POST | `decisionData.trialOutcomeCategory`, `decisionData.appealOutcomeCategory` | Per-decision granularity (vs. per-proceeding) |
| 1 | Binary flags | Fintiv addressed (Y/N) | decision PDF | Fetch `documentData.fileDownloadURI` → parse text. 500-char OCR preview is insufficient. | Decision PDFs live in Patent File Wrapper Documents bucket (1.2M/wk) |
| 1 | Binary flags | 325(d) addressed (Y/N) | decisions POST | `decisionData.statuteAndRuleBag` contains `35 USC 325` — structured enum, exact match | **Free** — no PDF fetch needed |
| 1 | Binary flags | Substantive grounds addressed (102, 103, 112) | decisions POST | `decisionData.issueTypeBag` — structured list of statutory grounds | **Free** — directly usable as categorical features |
| 1 | Binary flags | Sotera stipulation filed | **external** | Not in PTAB API — requires district-court cross-reference (Docket Navigator or `PTLITIG` bulk probe) | **Gap** — flagged in domain notes |
| 2 | Ordinal Fintiv ratings | 5-point rating per sub-factor (6 factors) | decision PDF | Fetch decision PDF → LLM / regex over full text under per-factor headings ("factor one" … "factor six") | Decisions follow formulaic structure — tractable once PDF is in hand |
| 3 | Temporal / regime | Petition filing date | proceedings | `petition_filing_date`, `accorded_filing_date` | |
| 3 | Temporal / regime | Institution decision date | proceedings | `institution_decision_date` | |
| 3 | Temporal / regime | Policy-era indicator | proceedings (derived) | Bucketed from `petition_filing_date` against the regime timeline in README §5 | Strong macro predictor |
| 4 | Metadata | Technology center / group art unit | proceedings | `technology_center`, `group_art_unit` | First two digits of art unit = tech center |
| 4 | Metadata | Patent age at petition | proceedings (derived) | `petition_filing_date` − `grant_date` | |
| 4 | Metadata | Counsel identity (petitioner / owner) | proceedings | `petitioner_counsel`, `owner_counsel` | Free text — needs normalization |
| 4 | Metadata | Real parties in interest | proceedings | `petitioner_real_party`, `owner_real_party` | |
| 5 | Document-structural | Discretionary-denial section length | documents (petition PDF) | PDF text extraction on petition | Petition OCR is *not* returned by decisions POST — need `/trials/{trial}/documents` → PDF → OCR |
| 5 | Document-structural | Prior-art reference count | documents (petition PDF) | Parsed from petition body | Same — petition-side, not decision-side |
| 5 | Document-structural | Petition two-part split (procedural vs. substantive) | documents (petition PDF) | Section detection on petition PDF | Same |
| 6 | Dispositive factor | Which Fintiv factor drove outcome | decision PDF | LLM classification over full decision text (fetched via `fileDownloadURI`) | Depends on (2) being extracted first |

---

## 3. What the API does *not* give us

Features requiring external sources — flagged early so we don't treat them as "free":

- **Sotera stipulation** — petitioner commits in district court, not at PTAB. Needs district-court docket data (e.g., Docket Navigator).
- **Parallel-litigation status / trial date proximity** (a Fintiv factor) — live district-court trial dates are not in the PTAB API.
- **Case-status ground truth beyond PTAB** — settlements, dismissals, appeals upstream.

These are the main blockers for a purely-PTAB-API pipeline.

---

## 4. Typical extraction path per feature type

- **Structured metadata** → `search_proceedings_post()` with `filters` on `trialMetaData.trialTypeCode` and `rangeFilters` on `petitionFilingDate`, trimmed via `fields`.
- **Statute / grounds signals** → `search_decisions_post()` reading `decisionData.statuteAndRuleBag` and `decisionData.issueTypeBag` — no text parsing required. This is the "free tier" of decision-side features.
- **Factor-level ratings, dispositive-factor, Fintiv addressed** → `search_decisions_post()` returns `fileDownloadURI` for each decision; fetch the PDF, OCR/parse, then regex/LLM over the full text. The inline `documentOCRText` is only a 500-char preview and is **not** sufficient.
- **Petition-side document features** (section length, prior-art count) → `get_trial_documents()` → filter to petition type → fetch petition PDF via `fileDownloadURI` → OCR/text-parse.
- **Tabular CSV export** (for external analysis) → `download_decisions()` with `format="csv"`. Restricted fields — don't request OCR or appeal fields here.
- **External signals (Sotera, parallel litigation)** → out of scope for the PTAB-only pipeline; revisit once district-court data source is chosen (probe `PTLITIG` bulk product first).

## 5. Cost model for a full ingestion

Rough estimate (per `rate_limits.md` constraints — serial, ~10 req/sec):

| Pass | Calls | Bucket | Wall time |
|---|---:|---|---|
| All proceedings (metadata) | ~200 | Metadata (5M/wk) | ~20 s |
| All decisions (structured + 500-char preview) | ~200 | Metadata (5M/wk) | ~30–60 s |
| Per-trial document indices (IPR only) | ~18K | Metadata (5M/wk) | ~30 min |
| **Decision PDFs** (institution + FWD, ~25K files) | ~25K | File Wrapper Documents (1.2M/wk) | **~40 min** + ~5–15 GB storage |
| Petition PDFs (selective, if building Priority-5 features) | up to ~18K | File Wrapper Documents (1.2M/wk) | ~30 min + tens of GB |

Total for the core modelling pass (everything except petition PDFs): **~70–80 min wall-clock, under 100K API calls, well under all weekly quotas.**
