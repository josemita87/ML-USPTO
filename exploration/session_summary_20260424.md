# Session Summary — April 24, 2026

## Focus

Full API surface mapping: what data actually exists in the ODP, what access patterns are practical at training-set scale, and what the ingestion cost model looks like. Started from questions about PTAB terminology and ended with a corrected ingestion plan driven by an empirical cap discovered in the decisions endpoint.

## What We Did Today

### 1. PTAB terminology and scope

Clarified distinctions that the ODP's naming conventions obscure:

- **Proceeding vs. decision vs. appeal** — a proceeding is the case (1 per trial); decisions are rulings *inside* it (many per proceeding); "appeal" at PTAB usually means ex parte appeals from examiner rejections, a different system entirely. CAFC appeals leave PTAB, they don't happen inside it.
- **Interferences (INT) and derivations (DER)** — legacy / rare proceeding types, out of scope. Filter to `trial_type IN ('IPR')`.
- **Office of Petitions vs. PTAB** — the ODP exposes a `/petition/decisions/*` API family that is NOT PTAB data. It's Office of Petitions rulings on procedural prosecution matters (PPH requests, PTA disputes, abandonment revivals). Wrong corpus for this project; skip.

Created `docs/ptab_scope_and_terminology.md` covering all of the above.

### 2. Bulk datasets catalog — what is and isn't there

Probed `GET /api/v1/datasets/products/search` live — 47 products, all patent- or trademark-side. Key finding: **no PTAB AIA trial bulk product exists.** Trial-side data must come from the live API.

Relevant bulk products for the patent-owner enrichment layer:
- `PASDL` / `PASYR` — patent assignments (NPE detection)
- `PTMNFEE2` — maintenance fees (lapse signals)
- `PTFWPRE` — full patent file wrapper
- `OACT` — office actions (prosecution difficulty)
- `PTLITIG` — litigation docket (potential Sotera source)
- CPC classification masters, PatentsView aggregates

Documented in `docs/bulk_datasets.md` with the full categorization, size footprints, and a recommended first-pass ingestion ordering.

### 3. ODP Simplified Query Syntax unlock

Read the ODP Query Spec PDF and empirically confirmed the POST + structured body syntax works on PTAB endpoints (`filters`, `rangeFilters`, `fields`, `facets`, `sort`, `pagination`). Field names use dotted paths (`trialMetaData.trialTypeCode`, `patentOwnerData.patentNumber`, etc.).

One facet call on `trialMetaData.trialStatusCategory` (no records iterated) revealed the full label distribution without touching OCR or PDFs:

- 19,246 proceedings: **IPR 18,058, CBM 602, PGR 558, DER 28**
- Status distribution: Institution Denied 6,015 / FWD 5,838 / Terminated-Settled 4,408 / Terminated 933 / Discretionary Denial 661 / FWD-Appealed 591 / Trial Instituted 351 / others small

This concretes the target-class decision from `domain_notes.md`.

### 4. Structured decision fields — partial Priority-1 unlock

The decisions endpoint returns `decisionData` with:
- `trialOutcomeCategory` — decision-level outcome label
- `statuteAndRuleBag` — statutes cited (e.g., "35 USC 325" → 325(d) detection, no text parsing)
- `issueTypeBag` — statutory grounds addressed ("102", "103", "112", …)
- `appealOutcomeCategory` — CAFC appeal result when applicable

This covers 325(d) and substantive-grounds features for free, no PDF needed.

### 5. Correction: `documentOCRText` is a 500-char preview

**Important correction to earlier claim.** The `documentOCRText` field returned by every PTAB endpoint (decisions search, documents endpoint, per-document detail endpoints) is globally capped at 500 characters. Enough for the case caption and judge names; cuts off before any substantive content. Confirmed across 3 FWD documents with PDF sizes 71KB / 328KB / 618KB — all returned exactly 500 chars.

**Consequence:** full decision text is NOT in the API response. Fintiv factor ratings, dispositive factor, and Fintiv-addressed detection all still require fetching the decision PDF via `documentData.fileDownloadURI` and parsing it locally. Earlier statements to the contrary (in drafts of `api_feature_map.md`, `bulk_datasets.md`, `rate_limits.md`) have been corrected.

### 6. Endpoint overlaps and choice of ingestion path

- `/trials/proceedings/search` = 1 row per trial, lean metadata — cheapest path to target labels + Priority 3/4 features.
- `/trials/decisions/search` = decision papers only, cross-trial, enriched with `decisionData`.
- `/trials/{trial}/documents` = all papers per trial (petitions, POPRs, exhibits, orders, decisions).
- The decision papers overlap between the last two — confirmed identical `documentIdentifier`s (e.g., `171322794` appears in both for IPR2022-01002). Choose one path per use case to avoid duplicate ingestion.
- Proceedings records expose a `trialMetaData.fileDownloadURI` that is a **per-trial zip** of every paper (441 MB for IPR2022-01002 — 145 PDFs). Usable as a bulk shortcut for deep single-trial analysis; impractical for full-corpus pulls (~8–9 TB).

### 7. Rate limits and cost model

Three quota buckets, quite different characteristics:

| Bucket | Weekly quota | Our usage |
|---|---|---|
| Metadata retrieval | 5M/wk | All `/trials/*` POSTs, catalog calls |
| Patent File Wrapper Documents | 1.2M/wk | PDF fetches via `fileDownloadURI` |
| Bulk Datasets Downloads | 20 per file per year | Bulk product zips |

**Burst = 1, serial only, 4–15 req/sec.** No parallelism. 429 requires min 5s delay.

Revised core-modelling ingestion estimate: **~70–80 min wall-clock, under 100K total API calls**, comfortably under all weekly quotas. Binding constraint is wall-clock from burst=1, not quota.

Documented in `docs/rate_limits.md`.

## Codebase Updates

### `src/data/client.py`

Added three POST-based methods alongside the existing GETs:

- `search_proceedings_post(q, filters, range_filters, fields, facets, sort, offset, limit)`
- `search_decisions_post(...)`
- `download_decisions(..., format="json"|"csv")` — exports tabular CSV/JSON; restricted field projection.

429 backoff changed from 2/4/8s to **5/10/20s** to meet the ODP-stated 5-second minimum.

Existing GET methods untouched — `fetch.py` and `exploration.py` callers still work.

### `docs/`

Four new docs, one significantly revised:

- **NEW `docs/ptab_scope_and_terminology.md`** — proceeding/decision/appeal distinction, in-scope trial-type filter, Petition Decision Search naming-collision warning, CAFC appeals label-handling note.
- **NEW `docs/bulk_datasets.md`** — 47-product catalog relevant/skip categorization, no-PTAB-bulk gap analysis, recommended ingestion ordering.
- **NEW `docs/patent_file_wrapper_features.md`** — tiered field list for patent-owner-side enrichment (NPE detection, prosecution difficulty, family size, etc.), fields to skip.
- **NEW `docs/rate_limits.md`** — three quota buckets, burst=1 serial constraint, revised cost model.
- **REVISED `docs/api_feature_map.md`** — POST endpoints with Simplified Query Syntax notes, structured decision fields, 500-char OCR-preview warning, Decisions-vs-documents overlap section, updated extraction paths, cost model (§5).
- `docs/ODP-API-Query-Spec.pdf` added as the canonical syntax reference.

## Corrections to Prior Understanding

1. **OCR text is NOT in the API response** (corrected — 500-char preview only). Earlier sessions and drafts assumed full decision text was available inline; it is not.
2. **Bulk datasets are orthogonal to IPR data** (confirmed). They are patent-side enrichment, not a substitute for `/trials/*`.
3. **`/trials/{trial}/documents` is not a replacement for `/trials/decisions/search`** — the overlap is identical physical PDFs for decision-type papers, but decisions carries structured analysis that documents does not.
4. **Ex parte appeals and interferences** are explicitly out of scope, filter via `trial_type`.

## Next Steps

- Probe remaining unknowns for ingestion planning: max `limit` per request (tested up to 25; spec doesn't state a ceiling), deep-offset behavior (many ODP endpoints cap around 10K), petition-endpoint pagination parameters (naïve `offset`/`limit` hit 400 on `/trials/{trial}/documents`).
- Probe `PTLITIG` bulk product contents to see whether it closes the Sotera / parallel-litigation gap cheaply.
- Wire up a small-batch pilot ingestion: one year of IPR proceedings + their decisions (structured fields only, no PDFs yet) → bootstrap label distribution and sanity-check the pipeline end-to-end.
- Decide target-variable class mapping given the now-known status distribution (binary: claims-survive vs. claims-invalidated? multi-class with Terminated-Settled as its own category? handling of Discretionary Denial?).
- Selective decision-PDF ingestion for Fintiv factor extraction — scope: institution decisions + FWDs, ~25K files, ~5–15 GB storage, ~40 min sequential.

## Data Scale (revised numbers from today's probes)

- **19,246 total PTAB proceedings** (facet probe 2026-04-24)
- **18,058 IPRs** specifically
- **~20K decisions** across all trials, ~25K if counting institution decisions + FWDs for IPRs
- **~500K–1M total trial papers** across all proceedings (documents endpoint granularity)
- **~8–9 TB if we pulled every trial zip** → selective paths only
