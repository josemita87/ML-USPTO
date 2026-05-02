# API ↔ Feature Map

A cross-reference between the USPTO PTAB endpoints and the feature families we care about. Use this as a quick map when designing extraction: *for feature X, which endpoint do I hit?*

> **Scope note.** Feature inclusion is governed by `../scope/prediction_scope.md` §4 (the T₀ leakage rule) and §8 (modeling assumptions). This doc is authoritative on *which endpoint returns what*; `../scope/prediction_scope.md` is authoritative on *what we're allowed to use*. Where the two appear to disagree, scope wins.

Client code: `src/ml_uspto/clients/uspto.py`. The proceedings endpoint and the documents/search endpoint serve **complementary** roles — see `proceedings.md` for the live-vs-frozen distinction.

**Authoritative API references**:
- Swagger UI / OpenAPI spec: <https://data.uspto.gov/swagger/index.html> — endpoint paths, request parameters, response body schemas.
- Query syntax reference: `ODP-API-Query-Spec.pdf` (in this directory).
- Empirical-probing note: prefer hitting endpoints with the live client to confirm real response shape before coding to the spec — Swagger examples include synthetic placeholder data.

---

## 1. Endpoints available

> **Proceedings and documents serve complementary roles.** Proceedings holds the freshest trial-level state (current status, terminationDate, etc.). Documents/search returns one row per filing — the document-specific data (`documentData`, and per-row `decisionData` on decision rows) is unique to each row, but the `trialMetaData` block is the trial-level header **denormalized at indexing time** and refreshed by a *separate, slower* indexer. There is no T₀-frozen view of the trial in either endpoint — pull all `trialMetaData` features from `proceedings/search`, and treat document-row `trialMetaData` as off-limits for features. See `proceedings.md` "What we observed" for the empirical lag table.

| Endpoint | Method in `USPTOClient` | Returns | Primary use |
|---|---|---|---|
| `POST /trials/proceedings/search` | `search_proceedings_post()` | One row per trial with the freshest trial / patent / party blocks. | **Primary source for features + the *coarse* label.** Static fields (patent, parties, art unit, dates ≤ T₀) → features. Mutable fields (`trialStatusCategory`, `terminationDate`, `institutionDecisionDate`, `latestDecisionDate`) → label only — but `trialStatusCategory` stops at `Final Written Decision` / `Final Written Decision - Appealed` and does not carry the FWD verdict. Pair with the decisions endpoint for label 1 vs 0 within FWD-reaching trials. |
| `POST /trials/documents/search` | `search_documents_post()` | Cross-trial document records. Each row carries `documentData` (per-row, including PDF link), `decisionData` (per-row, populated only on decision-type rows), and `trialMetaData` (trial-level, denormalized — lagged vs proceedings). | **Petition row discovery + per-row decisionData.** Current v1 uses a corpus-wide `PETITION` / `Paper` scan and reads only `documentData.*` from the picked petition row. Filter to `"FINAL"` or use decisions/search for label rows. *Do not* use `trialMetaData` from this endpoint as a feature. |
| `POST /trials/decisions/search` | `search_decisions_post()` | Structured decision metadata + a **500-char OCR preview** (`documentData.documentOCRText`). Full decision text is **not** returned. | Alternative label-assembly path when you want decision rows directly. Equivalent to documents/search filtered to decision categories, but pre-filtered by the API. |
| `POST /trials/decisions/search/download` | `download_decisions()` | CSV or JSON file attachment of decision metadata | Fast tabular export of decisions only; **restricted field projection** — rejects `documentOCRText`, `fileDownloadURI`, `appealOutcomeCategory`. |
| `GET /trials/{trial_number}/documents` | `get_trial_documents()` | Full filing list for a trial (capped at 25 records, no pagination). | Per-trial deep dive during exploration. Not used at corpus scale due to the 25-record cap. |
| `GET <documentData.fileDownloadURI>` | `download_pdf()` | Single PDF. | Current use: original-FWD PDFs for label fallback only. Deferred v2 use: petition PDFs for petition-text features. |
| `GET <trialMetaData.fileDownloadURI>` | *(not wrapped)* | Whole-case ZIP of every filing's PDF (~50–500 MB / case). | Reserved for one-off deep dives. Not used at corpus scale. |
| `GET /trials/proceedings/{trial_number}` | `get_proceeding()` | Single proceeding's full record + whole-case `trialMetaData.fileDownloadURI`. | Use when you need the whole-case ZIP URI for one specific trial. |
| `GET /applications/{applicationNumberText}` | `get_application()` | Full patent file wrapper — bibliographic, events, continuity, assignments, PTA, attorneys. The 7 sub-paths (`/transactions`, `/adjustment`, `/continuity`, `/foreign-priority`, `/assignment`, `/attorney`, `/documents`) are narrow projections of the same record. | **Patent-side feature enrichment.** Match key from `proceedings.patentOwnerData.applicationNumberText`. Dedup by `applicationNumberText` (joinder cases share patents). ⚠ `eventDataBag` carries `TRIALPET`/`TRIALGRT`/`TRIALFWD` — **the label** — and must be T₀-filtered before aggregation. See `patents.md`. |

All `/trials/*` calls above fall in the **metadata retrieval bucket** (5M calls/week combined, serial-only, burst=1). The `/applications/*` calls sit in a separate bucket — see `rate_limits.md` for full constraints.

> ⚠ **GET vs POST.** `/trials/documents/search` and `/trials/decisions/search` are **POST-only** — GET returns 404. Same is *not* true of `/trials/proceedings/search` (both methods work; POST is the structured-query form).

> ⚠ **`/search/download` endpoints sit in a stricter bucket than `/search`.** Empirically observed 2026-04-25: same key, same IP, same minute — `/proceedings/search/download?q=IPR2026-00339` returned `{"message":"Forbidden"}` (HTTP 403) while `/proceedings/search` and path-style endpoints returned 200. For ingestion at scale, prefer the paginated POST `/search` route over `/search/download` until this is diagnosed.

> ⚠ **`documentTypeName` is unindexed.** Empirical (2026-04-26): filtering or faceting on `documentData.documentTypeName` returns empty. Use `documentData.documentCategory` (case-insensitive — `"PETITION"` and `"Petition"` both work).

### The data model — denormalized, with two indexer cadences

PTAB data is conceptually a one-to-many hierarchy (Trial → Documents → PDF bytes). The API exposes it through two endpoints that differ on **indexer cadence**, not on schema:

```mermaid
flowchart TB
    subgraph Proc["proceedings/search row<br/>(fast indexer — refreshed within days of each event)"]
        TH1[trialMetaData<br/>freshest trialStatusCategory<br/>freshest terminationDate, etc.]
        POD1[patentOwnerData]
        PET1[regularPetitionerData]
    end
    subgraph Docs["documents/search row<br/>(slow indexer — same trial header,<br/>refreshed on a separate cadence)"]
        TH2[trialMetaData<br/>trial-level header denormalized,<br/>lag = minutes to years per trial]
        POD2[patentOwnerData]
        PET2[regularPetitionerData]
        DOC[documentData<br/>this document + fileDownloadURI<br/>per-row, fresh]
        DD[decisionData<br/>per-row, populated only<br/>on decision-type rows]
    end
    Docs --> PDF[PDF bytes via<br/>documentData.fileDownloadURI]
```

**Implication for the leakage rule.** Neither endpoint returns a T₀-frozen view. Both return a "current snapshot" of `trialMetaData` from their respective indexers, which differ in how stale they are. The proceedings row is fresher and is the canonical source for both features (static / pre-T₀ fields only) and label (mutable post-T₀ fields). The documents row's `trialMetaData` is *also* live state, just lagged — never assume it's T₀-frozen. Per-row fields on the documents row (`documentData`, `decisionData`) are fresh and unique to each filing — those are the documents endpoint's actual value.

**Same trial number filter, multiple return shapes:**

| Call | Rows for one trial | Each row represents |
|---|---:|---|
| `/proceedings/search` | 1 | the case (header), freshest state |
| `/documents/search` filter `documentCategory: PETITION` | 1 (typically) | the petition filing — `documentData` is fresh, `trialMetaData` is lagged |
| `/documents/search` filter `documentCategory: FINAL` | 0–N | terminating-FWD rows with populated `decisionData` |
| `/documents/search` no document filter | ~30–150 | one filing per row |
| `/decisions/search` | 0–N | one decision-type filing (filtered subset of documents/search) |
| `GET <trialMetaData.fileDownloadURI>` | (binary) | every PDF in the case, zipped |

### Two distinct `fileDownloadURI` fields

The same field name appears at two granularities and means different things — easy to confuse:

| Source | What it points to | Size |
|---|---|---|
| `trialMetaData.fileDownloadURI` (on a proceeding record) | **Whole-case ZIP** of every filing's PDF | ~50–500 MB per case (probed: 202 MB / 33 PDFs for IPR2026-00339) |
| `documentData.fileDownloadURI` (on a document or decision record) | **Single PDF** of one specific filing | ~0.5–25 MB per file |

Both are direct downloads via the authenticated session (`X-API-Key`). Reuse `client.session.get(uri)` rather than building a fresh request. The whole-case zip is *not* what `/search/download` returns — that endpoint exports search-result metadata as a CSV/JSON file and contains zero PDF content.

> ⚠ **`documentOCRText` is a 500-char preview, not the full text.** Confirmed empirically 2026-04-24: every endpoint that returns `documentOCRText` (decisions search, documents endpoint, per-document detail endpoints) caps it at 500 characters — enough for the case caption and judge names, nothing substantive. For full decision text you must fetch the PDF via `documentData.fileDownloadURI`. This is a global API behavior, not a `fields`-projection artifact.

> ⚠ **Naming collision:** the ODP catalog also exposes `/api/v1/petition/decisions/*` ("Petition Decision Search"). This is **not** PTAB data — it is the USPTO Office of Petitions' rulings on procedural prosecution matters (PPH requests, PTA disputes, abandonment revivals). Wrong corpus for this project. Telltale: `finalDecidingOfficeName: "OFFICE OF PETITIONS"` in the record. See `../scope/ptab_scope_and_terminology.md` §3.

### Decisions vs. documents — overlap, not duplication

The decisions endpoint is a **filtered subset of documents/search**, restricted to decision-type rows. Confirmed empirically on `IPR2022-01002`: the 3 records from `/trials/decisions/search` have identical `documentIdentifier`s to 3 of the 145 records from `/trials/{trial}/documents` — same PDFs, same OCR text. The cross-trial `/trials/documents/search` carries `decisionData` denormalized onto every row, so for label assembly you can either:

- **Pull FWD rows from decisions/search.** Each FWD-type row has its own populated `decisionData` block (`trialOutcomeCategory`, `issueTypeBag`, `statuteAndRuleBag`, `decisionIssueDate`) plus document metadata. In the current IPR corpus, `trialOutcomeCategory` is not granular enough for the binary verdict, so label resolution uses status first, then FWD title text, then cached FWD PDF text. Filtering documents/search to decision categories is functionally equivalent for exploration; the implemented path uses decisions/search.
- **Pull *coarse* label state from proceedings/search.** `trialMetaData.trialStatusCategory` resolves trials that *never reach* an FWD (`Terminated-Settled`, `Institution Denied`, `Discretionary Denial`, `Terminated`, …) — those map directly to label 0. But for trials that *do* reach an FWD, the proceedings status stops at `Final Written Decision` / `Final Written Decision - Appealed` and **does not encode the verdict** — you cannot tell label 1 ("all claims unpatentable") from label 0 ("mixed" or "all patentable") without the decisions endpoint. `ml_uspto.parse.labels` therefore needs both: proceedings for the non-FWD label-0 buckets, decisions for the FWD verdict. See `proceedings.md` "The proceedings status stops at FWD reached" for the empirical taxonomy.

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

> Feature inclusion is gated by `../scope/prediction_scope.md` §4 / §8. Rows below marked "label-only" or "out of scope" are accessible via the API but disallowed as features under the current scope; they're listed here so the endpoint surface stays complete.

**Where each row of the model matrix comes from:**
- *Trial inventory + label* → `POST /trials/proceedings/search` (live trial state — canonical list of all 18K IPRs).
- *Petition row / PDF URI* → `POST /trials/documents/search` corpus-wide with `documentData.documentCategory IN ["PETITION", "Paper"]`, then run the petition picker per `trialNumber` (see `proceedings.md`). Read only `documentData.*` from the picked row (`fileDownloadURI`, `documentFilingDate`, etc.). The `trialMetaData` block on this row is **lagged**, not frozen at T₀, and may carry post-T₀ values — pull all `trialMetaData` features from `proceedings/search` instead.
- *Petition-text features* → deferred v2. Current v1 stores the petition PDF URI but does not fetch or parse the petition PDF.

| # | Feature family | Specific feature | Endpoint | Source field / derivation | Status under prediction_scope |
|---|---|---|---|---|---|
| — | **Target** | Trial outcome | proceedings/search (live state) | `trialMetaData.trialStatusCategory` | Label |
| — | **Target** | Terminating-FWD outcome | decisions/search + optional FWD PDF text | Original-FWD row identifies `documentTitleText`, `documentIdentifier`, `fileDownloadURI`, and `decisionIssueDate`; title / cover-page regex gives the binary verdict | Label only — terminating FWD per §3 |
| 1 | Petition-text | Fintiv addressed (Y/N) | **petition PDF** | Petition §IV factor headers (seven phrasing patterns) | **In scope (Tier A).** `mentions_fintiv_factors` — fires when ≥3 distinct factor indices appear. |
| 1 | Petition-text | Sotera stipulation present | **petition PDF** | Petition §IV.4 phrase match ("will not pursue" / "stipulate" / "cease asserting") | **In scope (Tier A).** `has_sotera_stipulation` — see `parse/schemas/patterns.py::PETITION_SOTERA_PATTERNS`. |
| 1 | Petition-text | Statute grounds asserted (102/103) | **petition PDF** | Petition §I.B grounds table — `§ 102` / `§ 103` regex variants | **In scope (Tier A).** `n_grounds_102`, `n_grounds_103`. §112 challenges are out of IPR scope. |
| 1 | Petition-text | n_grounds | **petition PDF** | Petition §I.B grounds-table headers ("Ground N" / "Challenge #N" / "Grounds N and M") | **In scope (Tier A).** Distinct-index count. |
| 1 | Petition-text | n_challenged_claims, n_prior_art_references | **petition PDF** | §I.B + exhibit list | **Deferred v2 (Tier 1).** Full feature catalog: `../features/admissible_documents_analysis.md` §2.6. |
| 2 | Decision-side structured | `statuteAndRuleBag` (e.g. `35 USC 325` for 325(d)) | documents/search filtered to `FINAL` (or decisions POST) | `decisionData.statuteAndRuleBag` | **Out of scope as feature** (§4 leakage). Available for label-set debugging only. |
| 2 | Decision-side structured | `issueTypeBag` (102/103/112 actually addressed by judges) | documents/search filtered to `FINAL` (or decisions POST) | `decisionData.issueTypeBag` | **Out of scope as feature** (§4 leakage). Useful for evaluating extraction accuracy of feature 1 above. |
| 2 | Decision PDF text | Fintiv factor ratings, dispositive factor | decision PDF | Full text → LLM/regex over per-factor headings | **Out of scope** (§4 leakage). Available for ground-truth Fintiv labels in evaluation only — see `../scope/ptab_scope_and_terminology.md` §5.4. |
| 3 | Temporal / regime | Petition filing date (T₀ itself) | proceedings/search | `trialMetaData.petitionFilingDate`, `trialMetaData.accordedFilingDate` | **In scope.** |
| 3 | Temporal / regime | `filing_year`, `filing_month` | proceedings/search (derived) | Calendar breakdown of `petitionFilingDate` | **In scope.** Year carries era control; month carries intra-year seasonality (§315(b) bunching, fiscal-year boundary, Director-memo timing). |
| 3 | Temporal / regime | Institution decision date | proceedings/search (live) | `trialMetaData.institutionDecisionDate` | **Out of scope** (§4 leakage). Strictly label-side; do not pull from any source as a feature. |
| 4 | Metadata | Technology center / group art unit | proceedings/search | `patentOwnerData.technologyCenterNumber`, `patentOwnerData.groupArtUnitNumber` | **In scope.** |
| 4 | Metadata | Patent age at petition | proceedings/search (derived) | `petitionFilingDate` − `patentOwnerData.grantDate` | **In scope.** |
| 4 | Metadata | Counsel identity (petitioner / owner) | proceedings/search; richer petition detail deferred | `regularPetitionerData.counselName`, `patentOwnerData.counselName`; richer detail from petition §VI.C is v2 text work | **In scope from proceedings today.** Free text — needs normalization. |
| 4 | Metadata | Real parties in interest | proceedings/search; richer petition detail deferred | `regularPetitionerData.realPartyInInterestName`, `patentOwnerData.realPartyInInterestName`; petition §VI.A is authoritative for joinder but full RPI-list extraction is Tier 1 v2 work | **In scope from proceedings today.** Frequency-encoded per CV fold. |
| 5 | Petition-text structural | Petition word count + utilization | **petition PDF** | §42.24 certification footer | **Deferred v2.** |
| 5 | Petition-text structural | Prior-art reference count + classification | **petition PDF** | Petition exhibit list | **Deferred v2.** Full taxonomy in `../features/admissible_documents_analysis.md` §6.1. |

---

## 3. What the API does *not* give us

Features that previously looked like they needed external sources, but are admissible through petition-text extraction (the petition is at T₀):

- **Sotera stipulation** — extracted in v1 as the Tier A `has_sotera_stipulation` flag from petition §IV.4 phrase matches ("will not pursue" / "stipulate" / "cease asserting" / etc.). Earlier drafts flagged this as needing district-court data; it is usually in the petition because the petitioner has every incentive to feature it prominently. Pattern catalog: `parse/schemas/patterns.py::PETITION_SOTERA_PATTERNS`. See `../scope/ptab_scope_and_terminology.md` §5.
- **Parallel-litigation status / trial date proximity** — partially captured in v1 as `mentions_fintiv_factors` (binary). Per-factor narrative ratings (jury date, schedule) still require either Tier 1 v2 structural extraction or PACER data. See `../scope/prediction_scope.md` §8.3.

Genuinely missing from a petition-only pipeline:

- **PTAB's internal Fintiv ratings** — only available in the Institution Decision text, which is post-T₀ and excluded by §4. Ground-truth labels for evaluation only.
- **District-court docket congestion / judge-level statistics** — would enrich Fintiv factor 3 but require PACER or Docket Navigator. Out of scope for the current pipeline.

---

## 4. Typical extraction path per feature type

- **Trial inventory** → `POST /trials/proceedings/search` filtered to `trialMetaData.trialTypeCode: "IPR"`, paginated. ~18K rows, one per trial.
- **Petition row + PDF URI** → `POST /trials/documents/search` with `documentData.documentCategory IN ["PETITION", "Paper"]`, paginated corpus-wide. Apply the petition picker from `proceedings.md` per trial. Use only `documentData.*`; there is no T₀-frozen header in the API.
- **Tier A petition-text features** (`n_grounds`, `n_grounds_102`, `n_grounds_103`, `has_sotera_stipulation`, `mentions_fintiv_factors`) → fetch PDF via `documentData.fileDownloadURI` from the picked row, pdfplumber-extract under `Stage.PETITION_TEXTS`, regex-aggregate at feature build. Tier 1/2 (declaration counts, prior-art reference taxonomy, embeddings) deferred v2.
- **Label** (terminating-FWD outcome) — pick one of:
  - `POST /trials/proceedings/search` for live `trialStatusCategory` + `terminationDate` per trial.
  - `POST /trials/decisions/search`, flattened into decision rows. Pick the original FWD per `docs/scope/prediction_scope.md` §3.1; use title text first and cached FWD PDF text as fallback for the granular verdict.
- **Tabular CSV export** (for external analysis only) → `download_decisions()` with `format="csv"`. Restricted fields — don't request OCR or appeal fields here.

## 5. Cost model

For the operational cost model under the petition-only scope, see **`../scope/prediction_scope.md` §5.4** (canonical). The earlier table here that estimated ~25K decision PDFs and 50–90 GB of petition storage assumed a richer pipeline that has since been cut — consult the scope doc for current numbers.
