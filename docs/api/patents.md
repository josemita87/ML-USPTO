# Patents endpoint — application file-wrapper enrichment

The `/api/v1/patent/applications/{applicationNumberText}` endpoint returns the **patent file wrapper** — the USPTO's record of one application's full prosecution history (events, assignments, continuity, term adjustment, attorneys, bibliographic data). We use it to enrich each IPR with structured patent-side features beyond what `proceedings/search` exposes (which only carries `patentOwnerData.{patentNumber, applicationNumberText, grantDate, technologyCenterNumber, groupArtUnitNumber, inventorName}`).

## What the endpoint actually returns

Probe on 2026-04-27 against `applicationNumberText=14709428` (the patent in IPR2022-01002). Eight paths exist under `/applications/{appNum}`:

| Path | Returns | Top-level keys (non-empty) |
|---|---|---|
| `/applications/{app}` *(base)* | full file wrapper for one application | `applicationMetaData`, `eventDataBag`, `parentContinuityBag`, `assignmentBag`, `patentTermAdjustmentData`, `recordAttorney`, `grantDocumentMetaData`, `correspondenceAddressBag`, `lastIngestionDateTime` |
| `/applications/search` | full file-wrapper search results | `count`, `patentFileWrapperDataBag` |
| `/applications/{app}/transactions` | events only | `eventDataBag` |
| `/applications/{app}/adjustment` | PTA only | `patentTermAdjustmentData` |
| `/applications/{app}/continuity` | family only | `parentContinuityBag` |
| `/applications/{app}/foreign-priority` | foreign filings only | `foreignPriorityBag` |
| `/applications/{app}/assignment` | assignments only | `assignmentBag` |
| `/applications/{app}/attorney` | attorneys only | `recordAttorney` |
| `/applications/{app}/documents` | filing PDFs (different shape — `documentBag`) | `documentBag` |

**The base path returns the union of the sub-paths.** The 7 sub-paths are narrow projections of the same `patentFileWrapperDataBag` record. Same cost-shape lesson as `proceedings/search`: prefer the full wrapper over seven targeted calls. For corpus ingest, use `/applications/search` filtered by `applicationNumberText` so one request can return up to the configured page/batch size of wrappers. Sub-paths are useful only for incremental refresh of one slice (e.g., re-pull just the assignment bag for an existing record).

## Match key

`proceedings.patentOwnerData.applicationNumberText` → `GET /applications/{appNum}`. We already have the key from the proceedings ingest. **Dedup by application number, not by trial number** — joinder cases attach multiple IPRs to the same patent (and the same `applicationNumberText`), so a per-trial fetch would re-hit the same wrapper N times.

## Critical leakage hazard — the events bag carries the label

`eventDataBag` interleaves the patent's whole life: prosecution events (filing, examiner actions, allowances, fee payments) and post-grant events (maintenance fees, expiration, **and the IPR trial events themselves**). For the probed patent, the bag contains:

| Event code | Description | Date |
|---|---|---|
| `TRIALPET` | Petition Requesting Trial | 2022-05-23 *(= T₀)* |
| `TRIALDEN` | Request for Trial Denied | 2022-12-05 |
| `TRIALGRT` | Request for Trial Granted | 2022-12-05 |
| `TRIALFWD` | **Termination or Final Written Decision** | 2026-04-22 *(= label)* |
| `EXP.` | Expire Patent | 2023-12-25 |
| `REM.`, `M2551`, `M2554` | Maintenance fee events | 2019–2023 |

`TRIALFWD` is literally our label. `EXP.` is post-grant and post-T₀. **Naively flattening the events bag leaks the label.** The same hazard applies to:

- `applicationMetaData.applicationStatusDate` — reflects the patent's status *now* (here, 2023-12-25 = expiration), not at T₀.
- `applicationMetaData.applicationStatusCode` — current status code (250 = expired). Static-ish for granted patents but flips on expiration / reexam outcome.
- `parentContinuityBag[*].parentApplicationStatusCode` — parent's status *now*.
- `assignmentBag[*]` rows with `assignmentRecordedDate >= petitionFilingDate` — post-T₀ ownership transfers (often triggered by IPR loss).
- `patentTermAdjustmentHistoryDataBag[*]` events after grant — though the *aggregate* PTA quantities (`aDelayQuantity`, `bDelayQuantity`, `cDelayQuantity`, `adjustmentTotalQuantity`) are computed at grant and frozen, so they're safe.

**T₀-filter rule.** Every dated bag is filtered to `< petitionFilingDate` *before* aggregation. Status-as-of-now fields are excluded entirely. The leakage filter belongs in the parser, not in the model — keeping it close to the API surface is the only way to avoid silent re-introduction during feature engineering.

## Field-by-field paths

| Feature group | Path on `patentFileWrapperDataBag[0]` | Leakage class |
|---|---|---|
| **Bibliographic (static)** | | |
| `application_filing_date` | `applicationMetaData.filingDate` | static, < T₀ |
| `effective_filing_date` | `applicationMetaData.effectiveFilingDate` | static, < T₀ |
| `application_type` | `applicationMetaData.applicationTypeCode` | static |
| `entity_size` | `applicationMetaData.entityStatusData.businessEntityStatusCategory` | static |
| `first_inventor_to_file` | `applicationMetaData.firstInventorToFileIndicator` | static |
| `national_stage` | `applicationMetaData.nationalStageIndicator` | static |
| `n_inventors` | `len(applicationMetaData.inventorBag)` | static |
| `inventor_country_codes` | `applicationMetaData.inventorBag[*].correspondenceAddressBag[*].countryCode` | static |
| `cpc_codes` | `applicationMetaData.cpcClassificationBag` | static, < T₀ |
| `uspc_class_subclass` | `applicationMetaData.uspcSymbolText` | static (legacy) |
| `class_section` *(see Assumptions)* | first letter of any `cpcClassificationBag[i]` | derived |
| **Patent term adjustment (frozen at grant)** | | |
| `pta_a_delay` | `patentTermAdjustmentData.aDelayQuantity` | static, < T₀ |
| `pta_b_delay` | `patentTermAdjustmentData.bDelayQuantity` | static, < T₀ |
| `pta_c_delay` | `patentTermAdjustmentData.cDelayQuantity` | static, < T₀ |
| `pta_total` | `patentTermAdjustmentData.adjustmentTotalQuantity` | static, < T₀ |
| `pta_applicant_delay` | `patentTermAdjustmentData.applicantDayDelayQuantity` | static, < T₀ |
| **Continuity (filtered to ≤ T₀)** | | |
| `n_parent_applications` | `len(parentContinuityBag)` | static |
| `parent_filing_dates` | `parentContinuityBag[*].parentApplicationFilingDate` | static, < T₀ |
| `continuity_types` | `parentContinuityBag[*].claimParentageTypeCode` | static (CON / DIV / CIP / PRO) |
| ⚠ `parent_status_now` | `parentContinuityBag[*].parentApplicationStatusCode` | **disallowed** — current status |
| **Events (T₀-filtered, aggregated)** | | |
| `n_events_pre_t0` | `len([e for e in eventDataBag if e.eventDate < T₀])` | static after filter |
| `prosecution_span_days` | `max - min` of pre-T₀ event dates | static after filter |
| `days_grant_to_petition` | `petitionFilingDate - patentOwnerData.grantDate` | static, ≥ 0 |
| `n_office_actions` | count of pre-T₀ events with code in `{CTNF, CTFR, ...}` | static after filter |
| `n_ids_filings` | count of pre-T₀ events with code in `{IDS, WIDS}` | static after filter |
| ⚠ All `TRIAL*` events | `eventDataBag[*]` | **disallowed** — TRIALFWD is the label |
| ⚠ All post-T₀ events | `eventDataBag[*]` with `eventDate ≥ T₀` | **disallowed** |
| **Assignments (T₀-filtered)** | | |
| `n_assignments_pre_t0` | count of `assignmentBag[*]` with `assignmentRecordedDate < T₀` | static after filter |
| `n_distinct_assignees_pre_t0` | distinct `assigneeBag[*].assigneeNameText` across pre-T₀ rows | static after filter |
| `days_since_last_assignment` | `T₀ - max(assignmentRecordedDate)` over pre-T₀ rows | static after filter |
| ⚠ Post-T₀ assignments | `assignmentBag[*]` with `assignmentRecordedDate ≥ T₀` | **disallowed** |
| **Attorneys / status (use with care)** | | |
| `n_attorneys_of_record` | `len(recordAttorney.attorneyBag)` | usually static; check date |
| ⚠ `applicationStatusCode` | `applicationMetaData.applicationStatusCode` | **disallowed** — status now |
| ⚠ `applicationStatusDate` | `applicationMetaData.applicationStatusDate` | **disallowed** — status now |

The full event-code dictionary is large (the probed patent alone has 47 distinct codes — `CTNF`, `IDSC`, `M2551`, `PA..`, `PILS`, `RCAP`, `WIDS`, ...) and not documented in the API spec. We aggregate by **prefix family** rather than enumerate codes (e.g., `M*` = maintenance fees, `CT*` = examiner correspondence, `TRIAL*` = banned). The full code list belongs in `config/patents/event_codes.yaml` once we lock the families empirically.

## Assumptions

| # | Assumption | Rationale |
|---|---|---|
| **A1** | **Classification encoding = `technologyCenterNumber` (already from proceedings) + CPC section letter (first letter of any `cpcClassificationBag` entry).** No CPC subclass, no full-code one-hot, no USPC. | TC alone smudges the software-vs-hardware split inside one center; full CPC has 250K leaves and 60+ codes per patent — too sparse. Section-only adds 8 dimensions on top of TC, captures the technology-area signal that drives PTAB FWD-rate variation, and stays interpretable. Confirmed 2026-04-27 after probing the bag (66 codes on the sample patent collapsed cleanly to sections H + G). |
| A2 | Multi-section patents → use **section of the first CPC code** as the primary, optionally a multi-hot section vector if the model can use it. The first CPC is the examiner's primary classification. | The probed patent's first code is `H04W 88/06` (wireless networks) — that matches `technologyCenterNumber=2400` (TC 2400 = networks/multiplex). Empirical alignment confirms first-CPC = primary. |
| A3 | Batch file-wrapper fetches through `/applications/search`; sub-paths only used for incremental refresh. | Search returns the same full-wrapper bag shape as the base path and reduces request count from O(applications) to O(applications / page_size). |
| A4 | Dedup fetch by `applicationNumberText`, not `trialNumber`. | Joinder cases attach multiple trials to the same patent; per-trial fetching duplicates. |
| A5 | T₀-filter applied at parse time, not at feature time. | The filter is a property of the API surface (events / assignments are interleaved with post-T₀ data). Pushing it downstream risks silent re-leakage during feature engineering. |
| A6 | Aggregate event codes by prefix family (`M*`, `CT*`, `TRIAL*`, ...), not by enumerating codes. | The full code dictionary is undocumented and varies by examination era; families are stable. Final family list locked in `config/patents/event_codes.yaml` after corpus probe. |

## Recommended ingestion flow

1. **From the proceedings frame**, build the unique set `apps = {row.applicationNumberText for row in proceedings}` (~10–13K applications across ~18K IPRs after joinder dedup).
2. **For each uncached batch of applications**, `POST /applications/search` filtered by `applicationNumberText`. Cache each returned wrapper under `data/raw/patents/{app}.json`. Rate-limit bucket: same `/api/v1/patent/*` family as proceedings.
3. **Flatten via `parse.engine.flatten(records, "patents")`** with a new `config/parsers/patents.yaml` mapping the static paths from the table above. The parser config does *not* try to flatten the dated bags directly — those go through a dedicated aggregator step.
4. **Aggregate the dated bags** in a second pass, parameterized by `petitionFilingDate` (joined in from proceedings on `applicationNumberText`). Output one `PatentFeatures` row per `(trialNumber, applicationNumberText)`. T₀-filter is applied here, once, in code that lives next to the parser.
5. **Schema**: one `Patent` model in `schemas/models.py` for the raw flatten + a `PatentFeatures` model for the aggregations. Two layers because the raw flatten still has variable-length bags; the feature row is one fixed shape.

## Cost model

~10–13K unique applications / configured batch size. The live API rejects `pagination.limit > 100`, so with `page_size=100` the current 11,844-application corpus is about **119 search calls** plus cache reads, not 11,844 individual GETs. Caching by `applicationNumberText` keeps re-runs cheap.

## What this endpoint is NOT for

- Not a label source. Use `/trials/proceedings/search` for `trialStatusCategory`, `terminationDate`, `latestDecisionDate`. The `TRIAL*` events here are *also* the label, but going through the patent endpoint to derive the label is the wrong direction.
- Not a substitute for the petition PDF. The `/applications/{app}/documents` sub-path returns prosecution PDFs (office actions, IDS, claims), not PTAB filings. The petition lives in `/trials/documents/search` — see `proceedings.md` "Petition coverage and the category-taxonomy drift".
- Not a way to recover `petitionFilingDate`. T₀ comes from `proceedings.trialMetaData.petitionFilingDate`. The patent-side `TRIALPET` events approximate it but can be off by hours-to-days and may have multiple entries (the probed patent has 4 — likely amended-petition refilings).
