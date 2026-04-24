# Patent File Wrapper as a Supplementary Feature Source

The USPTO Open Data Portal `patent/applications/search` endpoint (the "patent file wrapper" data) is a separate system from the PTAB API. It describes how a patent was *prosecuted and granted* — not whether it was later challenged at the PTAB. It is not a replacement for the PTAB endpoints that source our target and institution-stage features, but it is a useful **patent-owner-side** enrichment layer.

Join key: `patentNumber` (also present on PTAB proceedings as `patent_number`) or `applicationNumberText`.

---

## 1. Fields worth extracting

Ordered by expected signal strength for IPR trial-outcome prediction.

### Tier 1 — likely highest-value additions

| Source field | Derived feature | Why it matters |
|---|---|---|
| `assignmentBag` (last entry) | Current assignee; NPE / operating-company flag | NPE-owned patents show very different IPR outcome distributions than operating-company patents. More reliable than PTAB's free-text `owner_real_party`. |
| `eventDataBag` | Count of office actions, RCEs, appeals during prosecution | Prosecution difficulty is a strong correlate of patent strength and IPR success. |
| `patentTermAdjustmentData.adjustmentTotalQuantity`, `bDelayQuantity` | Total PTA and USPTO-delay components | Cheaper proxy for contested prosecution; complements `eventDataBag`. |

### Tier 2 — useful, moderate signal

| Source field | Derived feature | Why it matters |
|---|---|---|
| `childContinuityBag` | Family size (count of children) | A large continuation family indicates commercial importance, which correlates with defense intensity at trial. |
| `foreignPriorityBag` | Has foreign priority (Y/N); count of jurisdictions | Foreign-filed patents skew toward higher-value cases. |
| `cpcClassificationBag` | CPC codes | Far more granular than PTAB's `technology_center`. Useful if tech-domain turns out to be predictive. |
| `entityStatusData.smallEntityStatusIndicator` | Small / undiscounted entity flag | Proxy for owner size and type. |
| `customerNumber` + `recordAttorney.attorneyBag[].registrationNumber` | Firm ID; attorney bar number | Structured IDs for counsel — allows clean rollup of PTAB's free-text `owner_counsel` to a firm identity. |

### Tier 3 — via linked bulk XML (`grantDocumentMetaData.fileLocationURI`)

From the Red Book grant XML:
- Claim count, independent claim count.
- Claim length, specification length.
- Classic "patent quality" textual features.

Only worth the ingestion cost if substantive claim features become a modeling priority.

---

## 2. Fields to skip

- `examinerNameText` — examiner-level effects are noisy and individually small.
- `correspondenceAddressBag` — largely redundant with `customerNumber`.
- `pgpubDocumentMetaData` — pre-grant publication text adds little over the granted claims.
- `parentContinuityBag` — less predictive than children (parents are about where the patent came from, not its downstream value).

---

## 3. Integration notes

- This endpoint lives on `data.uspto.gov`, separate from the PTAB `/trials/*` endpoints. It has its own auth and rate limits — treat it as a second data source in the pipeline, not a sub-resource of the PTAB client.
- The Swagger examples are synthetic (e.g., `groupArtUnitNumber: "TTAB"`, `applicationTypeCategory: "electronics"`, placeholder "John Smith" entries mixed with real names). Do not shape code to the example — probe the endpoint with a real IPR'd patent number from our dataset first and confirm which fields are actually populated.
- Recommended entry point: **NPE detection via `assignmentBag`**. Cheap to compute, likely the strongest individual add, and doesn't require PDF/XML ingestion.

---

## 4. Relationship to other docs

- `api_feature_map.md` — PTAB-side endpoints and features (our primary pipeline).
- `ptab_scope_and_terminology.md` — what counts as in-scope PTAB data.
- `bulk_datasets.md` — bulk-download catalog for ingesting these features at training-set scale.
- `rate_limits.md` — quota buckets and concurrency limits (file-wrapper metadata is 5M/wk; PDF document fetches likely 1.2M/wk).
- `exploration/domain_notes.md` — why specific features matter, per the domain expert.

This document covers the **patent-prosecution-side enrichment layer only**.
