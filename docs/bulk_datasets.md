# USPTO Bulk Datasets

The USPTO Open Data Portal exposes a catalog of downloadable bulk datasets separate from the live per-record APIs. Catalog endpoint:

```
GET https://api.uspto.gov/api/v1/datasets/products/search
```

This is a **discovery** endpoint — it returns product metadata and direct `fileDownloadURI`s. To actually ingest data, you download and parse the zipped files.

Use bulk ingestion when API-based per-record extraction becomes impractical at training-set scale. Use the live APIs for exploration and for freshness (bulk files refresh on a product-specific cadence — daily/weekly/yearly).

Probed 2026-04-24 — **47 products** in the catalog. Subset below is filtered for this project.

---

## 1. PTAB bulk status

**There is no PTAB AIA trial bulk product in this catalog.** IPR / PGR / CBM proceedings, decisions, and documents do not appear as downloadable bulk datasets. The only "TTAB" entries are the **Trademark** Trial and Appeal Board, which is a different system.

However, the gap is smaller than a "no bulk = manual per-record fetches" framing would suggest. Two live PTAB endpoints, both confirmed 2026-04-24, close most of it:

- **`POST /trials/proceedings/search`** and **`POST /trials/decisions/search`** accept the ODP Simplified Query Syntax body (`filters`, `rangeFilters`, `fields`, `facets`, `sort`, `pagination`). Surgical server-side filtering + field trimming makes paginated API ingestion practical at the scale of the full PTAB docket (~19K proceedings, ~20K decisions). Structured decision fields (`statuteAndRuleBag`, `issueTypeBag`, `trialOutcomeCategory`, `appealOutcomeCategory`) come back for free — these cover 325(d), substantive grounds, and outcome labels without text parsing.
- **`POST /trials/decisions/search/download`** exports the same result set as CSV or JSON (file attachment). Field projection is restricted — it does **not** support `documentData.documentOCRText` or `decisionData.appealOutcomeCategory`. Use it for tabular metadata exports, not rich text.

**Caveat on decision text:** the `documentOCRText` field returned by the decisions endpoint is capped at 500 characters — a case-caption preview, not the full decision. For Fintiv factor ratings, dispositive factor, or any full-text feature, you must still fetch the decision PDF via `documentData.fileDownloadURI` and OCR/parse it locally. See `api_feature_map.md` for details.

**Implication:** trial-side data comes from the live `/trials/*` API, not bulk zips. Structured metadata + statutes + grounds come from API calls (in the metadata bucket, 5M/wk); full decision text still needs ~25K PDF fetches (in the Patent File Wrapper Documents bucket, 1.2M/wk). Bulk downloads remain useful for the patent-owner-side enrichment layer described in `patent_file_wrapper_features.md`.

---

## 2. Relevant products

### Patent-owner-side features (priority order)

| Product ID | Title | Frequency | Size | Use |
|---|---|---|---|---|
| `PASDL` / `PASYR` | Patent Assignment XML (Daily / Annual) | Daily / Yearly | 2 GB / 5 GB | **NPE detection via assignment chain** — highest-value patent-side feature. |
| `PTMNFEE2` | Patent Maintenance Fee Events | Weekly | 3 GB | Patent-lapse signals — commercial-importance proxy. |
| `PTFWPRE` | Patent File Wrapper (Bulk) | Weekly | 63 GB | Full file wrapper — events, PTA, continuity, assignments in one product. 2001–2026 in 10-year zips. |
| `PTFWPRD` | Patent File Wrapper (Daily) | Daily | 24 GB | Daily deltas for keeping an ingested copy fresh. |
| `OACT` | Office Actions Weekly | Weekly | 65 GB | Full office-action history — direct measure of prosecution difficulty. |
| `PTOFFACT` | Office Action Research Dataset | Yearly | 3 GB | Research-formatted office action data. |
| `ECOPAIR` | PatEx Research Dataset | Yearly | 278 GB | Academia-friendly PAIR (prosecution history) export. Comprehensive but large. |
| `ECORSEXC` | Patent Assignment Data for Academia | Yearly | 50 GB | Cleaner, research-formatted version of assignments. |
| `PTAPPCLM` | Patent Claims Research Dataset | Yearly | 44 GB | Claim-level features (count, independent claims, length). |
| `CPCMCPT` / `CPCMCAPP` | CPC Classification Master (Grants / Applications) | Monthly | ~12 GB each | Clean CPC code tables for tech-domain features. |

### Litigation context (potential Sotera / parallel-litigation signal)

| Product ID | Title | Frequency | Size | Use |
|---|---|---|---|---|
| `PTLITIG` | Patent Litigation Docket Report Data Files | Yearly | 5 GB | Potential source for the **Sotera stipulation** / parallel-litigation signal flagged as a gap in `exploration/domain_notes.md`. May partially replace Docket Navigator dependency — probe contents before relying. |

### Patent text (optional, for substantive claim/spec features)

| Product ID | Title | Size |
|---|---|---|
| `PTBLXML` | Patent Grant Bibliographic (Front Page) — XML | 19 GB |
| `PTGRXML` | Patent Grant Full Text — XML | 126 GB |
| `APPXML` | Patent Application Full Text — XML | 162 GB |

`PTBLXML` is the lightweight option for front-page metadata only.

### PatentsView research aggregates (optional)

`PVANNUAL`, `PVSORTED`, `PVGPATDIS`, `PVPGPUBDIS`, `PVGPATTXT`, `PVPGPUBTXT` — pre-aggregated, disambiguated data from the PatentsView research program. Useful for macro stats and sanity checks; less direct for per-patent features.

---

## 3. Skip

- **All trademark products**: `TRCFECO2`, `TRTDXFAP`, `TTABTDXF`, `TTABYR`, `TRTYRAP`, `TRTYRAG`, `TRASECO`. Not patent data.
- **Image-heavy**: `PTGRMP2` (14 TB), `APPMP2` (14 TB), `APPDT` (4 TB), `PTGRDT` (2.8 TB). PDF/TIFF only.
- **Legacy formats**: `PTGRAPS`, `PTBLAPS`, `PTGRSGM`, `PTBLSGM`, `PTGRDSGM` — superseded by XML equivalents.
- **Niche research datasets**: `HISTEXC`, `MOONSHOT`, `ECOPATAI` (unless doing AI-patent subset), `PTAPOATH`, `PEDSJSON` / `PEDSXML` (pre-2001 only).

---

## 4. Recommended first ingestion pass

In priority order, targeting highest feature-value-per-GB:

1. `PASDL` / `PASYR` — NPE detection. Cheap (few GB), high signal.
2. `PTMNFEE2` — lapse events. Small (3 GB), informative.
3. `PTFWPRE` — file wrapper (events, PTA, continuity, assignments together).
4. `PTLITIG` — probe whether it covers Sotera-relevant district-court context.

Defer `OACT`, `ECOPAIR`, `PTGRXML`, `APPXML` unless the first pass proves insufficient — these are 60–280 GB each and require streaming parsers and real storage infrastructure.

---

## 5. Operational notes

- The catalog response has `bulkDataProductBag` as a list whose elements may themselves be nested lists — flatten before iterating.
- Each product's `productFileBag.fileDataBag` lists the actual zip files with `fileDownloadURI`s, date ranges, and sizes.
- Auth: the same USPTO API key (`X-API-Key` header) works for the datasets endpoint and for the downloads.
- Product IDs and the catalog itself are observed from a live probe on 2026-04-24 — re-verify before committing to a specific ID, as the USPTO occasionally reorganizes the catalog.
- **Hard rate limit on bulk downloads**: 20 downloads per file per year (higher for XML), 5 files per 10 seconds per IP, redirected URLs signed with 5-second expiry. Plan for download-once-cache-locally. See `rate_limits.md`.

---

## 6. Relationship to other docs

- `api_feature_map.md` — PTAB-side endpoints and features, including the POST Simplified Query Syntax that makes API ingestion viable at scale.
- `patent_file_wrapper_features.md` — which patent-owner-side fields are worth extracting (the "what"); this doc covers the "how at scale."
- `ptab_scope_and_terminology.md` — in-scope PTAB trial types.
- `ODP-API-Query-Spec.pdf` — full reference for the Simplified Query Syntax used by POST endpoints.
