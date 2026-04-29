# Patent file wrapper — feature dictionary

> Companion to [`../api/patents.md`](../api/patents.md). That doc covers **where** each feature lives in the JSON response and **whether** it leaks T₀. This doc covers **what each feature means** in plain English and **what signal** it is expected to carry for IPR trial-outcome prediction.

The "patent file wrapper" is USPTO's record of one application's full prosecution history (events, assignments, continuity, term adjustment, attorneys, bibliographic data). We pull it from `GET /api/v1/patent/applications/{applicationNumberText}` to enrich each IPR with patent-side features beyond what `proceedings/search` exposes.

Join key: `applicationNumberText` (already present on PTAB proceedings as `patentOwnerData.applicationNumberText`). Dedup the *fetch* by application number — joinder cases attach multiple IPRs to the same patent, but the file wrapper is identical.

---

## How to read this doc

Each feature entry includes:

- **What it captures** — the underlying patent-prosecution concept.
- **Signal prior** — pre-modeling guess (`strong` / `moderate` / `weak` / `banned`). Based on domain-expert input + IPR literature, **not** empirical importance. Guides "is this worth the engineering cost" decisions, not feature selection.
- **Banned reasons** (when applicable) — why the feature would leak T₀ or the label.

Glossary aside for non-lawyers:
- **T₀** = `petition_filing_date`. Anything dated ≥ T₀ is *future* relative to the prediction problem and would leak.
- **IPR** = Inter Partes Review, the PTAB proceeding we're predicting outcomes of.
- **Prosecution** = the back-and-forth between the patent applicant and USPTO that ends in a granted patent (or abandonment).

---

## 1. Bibliographic — the patent's identity card

### `filing_date` / `effective_filing_date`
**What**: The day this application was filed (`filing_date`) and the priority-claim date used for prior-art purposes (`effective_filing_date`). They differ when a patent claims priority through earlier parents — `effective_filing_date` reaches back to the earliest ancestor. USPTO populates `effectiveFilingDate` inconsistently; cross-check against the continuity bag for the real earliest date. **Signal**: weak directly (it's a date), but feeds derived features like `days_grant_to_petition`.

### `application_type`
**What**: One of `UTL` (utility — ~95% of patents, "how an invention works"), `DES` (design — ornamental appearance only), `PLT` (plant), `REI` (reissue), `PCT` (international before US entry). **Signal**: weak. Almost everything we see is `UTL`; design patents have very different IPR dynamics but are rarely IPR'd.

### `entity_size`
**What**: Fee tier USPTO charges the applicant. `Regular Undiscounted` = large company (full fees), `Small` = <500 employees (50% off), `Micro` = independent inventor / very small (75% off). **Signal**: moderate. Petitioners disproportionately attack large-entity patents — those tend to be commercially valuable enough to litigate hard.

### `first_inventor_to_file`
**What**: Binary flag for whether the patent falls under post-March-2013 America Invents Act ("first to file") rules vs the pre-2013 "first to invent" regime. The two have slightly different prior-art definitions. **Signal**: weak. Mostly a date proxy at this point — almost all post-2013 filings are `Y`.

### `national_stage`
**What**: Was this patent originally filed internationally via PCT (Patent Cooperation Treaty) and then *nationalized* into the US? `False` = filed directly at USPTO. **Signal**: weak. PCT patents skew toward larger entities targeting multiple countries.

### `n_inventors`
**What**: Count of named inventors on the patent. **Signal**: weak. High counts (5+) often track corporate research; sole-inventor patents often track individuals / small businesses.

### `tech_center` / `group_art_unit`
**What**: USPTO sorts applications into ~9 Tech Centers (TC) by subject matter, then sub-buckets called Art Units (AU). The first two digits are the TC, the full four digits are the AU.

| TC range | Subject |
|---|---|
| 1600s | Biotech / pharma |
| 2100s | Computer architecture / software |
| 2400s | Networks / multiplex |
| 2600s | Communications |
| 2800s | Semiconductors / electronics |
| 3600s | Transportation / construction / agriculture |
| 3700s | Mechanical engineering / consumer products |

**Signal**: strong. IPR institution and FWD-invalidation rates vary substantially by TC. Software (TC 2100s, parts of 3600s) is challenged most often and invalidated most often. Sourced from `patentOwnerData.technologyCenterNumber` in proceedings, not from the file wrapper — listed here because it's logically a patent attribute.

### `n_cpc` / `primary_section`
**What**: CPC (Cooperative Patent Classification) is a tree-structured technology tagging system. `n_cpc` = how many codes were assigned. The first letter of any code is the **section**:

| Section | Field |
|---|---|
| A | Human Necessities (food, clothing, footwear, agriculture) |
| B | Performing Operations / Transporting |
| C | Chemistry, Metallurgy |
| D | Textiles, Paper |
| E | Civil Engineering / Mining |
| F | Mechanical Engineering, Lighting, Heating |
| G | Physics (computing, optics, instruments) |
| H | Electricity |
| Y | Cross-cutting tags (e.g. climate-related) |

**Signal**: moderate. CPC section is a coarser version of TC and the two correlate strongly, but section captures cross-TC families (e.g., "anything electrical").

### `grant_date`
**What**: When the patent issued. Combined with `filing_date` it gives prosecution duration; combined with T₀ it gives `days_grant_to_petition`. **Signal**: moderate via derived features. Fast prosecution (≤ 1 year filing→grant) often signals an "easy" application or a continuation that benefited from prior parent work.

---

## 2. Patent Term Adjustment — extra patent life for USPTO delays

US utility patents normally last 20 years from the earliest priority filing date. PTA *adds days* to compensate for USPTO procedural slowness, *minus* days where the applicant dragged feet. All four delay quantities are computed at grant and frozen — they're safe pre-T₀ features.

### `pta_a_delay`
**What**: Days added because the examiner missed statutory deadlines (e.g., didn't issue first office action within 14 months). **Signal**: moderate. High A-delay = USPTO sat on the application; not directly applicant's fault.

### `pta_b_delay`
**What**: Days added because total prosecution exceeded 3 years. Captures slow prosecution overall regardless of cause. **Signal**: moderate. High B-delay correlates with contested / many-rounds prosecution, which can indicate weaker claims.

### `pta_c_delay`
**What**: Days added for time spent in interferences, secrecy orders, or successful appeals. **Signal**: weak. Rare; when present, signals an unusual prosecution path.

### `pta_total`
**What**: Net adjustment days actually granted. Computed as `max(A, B) + C − applicant_delay`. **Signal**: moderate. Single-number summary of "how much slower than 3 years was this prosecution."

### `pta_applicant_delay`
**What**: Days *subtracted* because the applicant took longer than 3 months to respond to office actions. **Signal**: moderate. High applicant delay can indicate a low-priority application or strategic delay.

---

## 3. Continuity — the patent family tree

### `n_parent_applications`
**What**: How many earlier applications this patent claims priority to.

Continuity types:
- **PRO** = provisional. Cheap 1-year placeholder that never becomes a patent itself but locks in a priority date.
- **CON** = continuation. Same written disclosure as parent, new claim set.
- **CIP** = continuation-in-part. Parent + new matter added.
- **DIV** = division. Parent had multiple inventions; USPTO required splitting them.

**Signal**: strong. Continuations are common in IPR-targeted patents — the owner held the family open to add claims that read on competitors' products. High continuity counts often correlate with patent thickets, portfolio plays, or NPE-style strategies.

### `parent_filing_dates`, `continuity_types`
**What**: Per-parent dates and type codes (vector-valued). Used for derived aggregates (oldest priority date, presence of CIP, etc.). **Signal**: moderate via derived features.

### `parent_status_now` (banned)
**What**: Each parent's `parentApplicationStatusCode`. **Banned**: parent status reflects state *now*, not at T₀ — a parent that subsequently expired or was invalidated would leak.

---

## 4. Events — the pre-T₀ prosecution timeline

The `eventDataBag` is a list of timestamped events with a 4-character code (`CTNF`, `WIDS`, `M2551`, etc.). USPTO has ~1,873 unique event codes across the corpus; a single patent typically has 30–60. We **filter to events with `eventDate < T₀`** and **drop all `TRIAL*` events entirely** (they are or imply the label), then **count by family**.

### Family taxonomy

Five categories follow USPTO's official taxonomy from Appendix B of the Public PAIR Transaction History Data Release. Three more (`MAINT`, `TRIAL`, `OTHER`) are added empirically.

| Family | What it represents | Example codes |
|---|---|---|
| **PE** | Pre-Examination — paperwork and routing in the first weeks before substantive examination | `IEXX`, `SCAN`, `OIPE`, `COMP`, `FLRCPT.O`, `PGPC` |
| **EX** | Examination — substantive examiner correspondence (rejections, allowances, IDS-considered) | `DOCK`, `CTNF`, `MCTNF`, `CTFR`, `MCTFR`, `IDSC`, `N/=.`, `XT/G` |
| **AA** | Applicant Action — filings the patent owner sent in (IDS, amendments, address changes) | `WIDS`, `M844`, `A...`, `A.PE`, `C.AD`, `RQPR`, `PA..` |
| **AD** | Administrative — notifications and metadata | `EML_NTR`, `EML_NTF`, `PTAC`, `PG-ISSUE` |
| **ISS** | Issuance — events around the actual patent grant (~2-month window) | `N084`, `IFEE`, `PGM/`, `WPIR`, `PILS` |
| **MAINT** | Maintenance fees — post-grant survival markers paid at 3.5/7.5/11.5 years | `M2551`, `M2554`, `M2555`, `REM.` |
| **TRIAL** | **banned** — events caused by the IPR proceeding itself | `TRIALPET`, `TRIALDEN`, `TRIALGRT`, `TRIALFWD`, `TRIALDIS`, `TRIALSTL` |
| **OTHER** | Catch-all for unmapped codes | reexam (`RX*`), appeal (`AP*`), abandonment (`ABN*`), long-tail rare codes |

The seed code→category lookup lives in `config/patents/event_codes.yaml`. After the smoke run we audit the top codes landing in `OTHER` and decide whether to promote any to a real family.

### Per-family count features

For each family except `TRIAL`, we emit `n_<family>_pre_t0` plus a sanity-check `total_events_pre_t0` sum.

- **`n_pe_pre_t0`** — *Signal*: weak. Mostly clerical; anomalously many can hint at filing-quality issues.
- **`n_ex_pre_t0`** — *Signal*: **strong**. Heaviest single signal in the family-count features. High counts mean many rejection rounds, many examiner exchanges → often weaker claims, more vulnerable in IPR. Low counts (first-action allowance) often mean stronger claims.
- **`n_aa_pre_t0`** — *Signal*: moderate / interpretation-dependent. Often dominated by IDS filings — high counts indicate the applicant cited a lot of prior art, which can make IPR harder for the petitioner (much of their art was already considered) or can flag heavy amendment activity (weaker original claims).
- **`n_ad_pre_t0`** — *Signal*: weak. Mostly opt-in for electronic notifications.
- **`n_iss_pre_t0`** — *Signal*: weak. Almost every granted patent has 4–6 ISS events; low variance.
- **`n_maint_pre_t0`** — *Signal*: moderate / strong. A patent that's already paid the 7.5-year fee is mature and commercially valuable enough that the owner has invested in keeping it alive. Petitioners disproportionately attack high-value patents.
- **`n_other_pre_t0`** — *Signal*: weak in v1; diagnostic. After the smoke run we'll see which codes dominate and either promote them to a real family (e.g., abandonment events to a new `ABANDON` family) or leave them aggregated.

### Other event-derived features

- **`prosecution_span_days`** — `max - min` of pre-T₀ event dates. *Signal*: moderate. Long span often correlates with contested prosecution.
- **`days_grant_to_petition`** — `petition_filing_date − grant_date`. *Signal*: **strong**. Indirectly proxies "when was the patent first litigated": short → litigated quickly after grant; long → sat dormant until a recent suit triggered the IPR. Computed at join time (Stage 4), not in the patent aggregator, because it needs `petition_filing_date` from proceedings.

### Banned event-derived features

- **All `TRIAL*` events** — banned. `TRIALFWD` *is* the label.
- **All post-T₀ events** — banned. Reflects future activity.

---

## 5. Assignments — ownership history

### `n_assignments_pre_t0`
**What**: Count of `assignmentBag` entries with `assignmentRecordedDate < T₀`. Includes original inventor→employer assignments, sales between companies, and security interests (loan collateral). **Signal**: moderate.

### `n_distinct_assignees_pre_t0`
**What**: Count of distinct `assigneeNameText` values across pre-T₀ rows. **Signal**: **strong**. High counts (3+) are textbook NPE / litigation-shop indicators — patents that have changed hands repeatedly before being asserted are disproportionately challenged. Caveat: "distinct" is a string match, so name-normalization matters (`Apple Inc.` vs `Apple Inc` vs `APPLE INC.` should collapse). Implement a normalizer or accept the noise in v1.

### `days_since_last_assignment`
**What**: `T₀ − max(assignmentRecordedDate)` over pre-T₀ rows. **Signal**: weak/moderate. A recent ownership transfer right before the IPR (e.g., 30 days) often signals a litigation-prep transfer.

### Post-T₀ assignments (banned)
Assignments dated ≥ T₀ are dropped — patents losing IPRs are sometimes transferred shortly after, leaking the outcome.

---

## 6. Attorneys

### `n_attorneys_of_record`
**What**: How many attorneys/agents are listed as authorized to prosecute this patent. Doesn't mean N people did the work — it means N are *authorized*. Typical for mid-sized firm representation: lead partner + associates + backup names. **Signal**: weak / moderate. Bigger firm representation may indicate more sophisticated prosecution strategy.

---

## 7. Status — banned (post-T₀)

### `applicationStatusCode` / `applicationStatusDate`
**What**: USPTO's current single-value status as of the last data refresh. Common values: `30` (in examination), `92` (notice of allowance mailed), `150` (patented case — granted, in force), `161` (abandoned for failure to pay issue fee), `250` (expired), `260` (reexam certificate issued), `265` (expired for failure to pay maintenance fees). **Banned**: reflects status *now*, not at T₀. If the patent has since been invalidated by the IPR we're trying to predict, this would directly leak the label.

---

## 8. Signal-prior summary

Pre-modeling expectation, not empirical importance.

| Strength | Features |
|---|---|
| **Strong** | `tech_center`, `n_distinct_assignees_pre_t0`, `n_parent_applications`, `days_grant_to_petition`, `n_ex_pre_t0`, `n_maint_pre_t0` |
| **Moderate** | `entity_size`, `pta_total`, `pta_b_delay`, `prosecution_span_days`, `primary_section`, `n_aa_pre_t0`, `n_assignments_pre_t0`, `grant_date` (via derived) |
| **Weak** | `n_inventors`, `n_attorneys_of_record`, `application_type`, `national_stage`, `first_inventor_to_file`, `n_pe_pre_t0`, `n_ad_pre_t0`, `n_iss_pre_t0`, `n_other_pre_t0` |
| **Banned** | `applicationStatusCode`, `applicationStatusDate`, `parent_status_now`, all `TRIAL*` events, all post-T₀ events, all post-T₀ assignments |

---

## 9. Fields we don't extract and why

- `examinerNameText` — examiner-level effects are noisy and individually small; would need careful encoding to avoid overfitting.
- `correspondenceAddressBag` — largely redundant with `customerNumber`.
- `pgpubDocumentMetaData` — pre-grant publication text adds little over the granted claims.
- `childContinuityBag` — child applications are post-T₀ filings; counting them would require date-filtering and adds little over `parentContinuityBag`-derived family-size features.

---

## 10. Relationship to other docs

- [`../api/patents.md`](../api/patents.md) — canonical JSON-path reference for `/applications/{appNum}` and the leakage-class table per field.
- [`../api/api_feature_map.md`](../api/api_feature_map.md) — PTAB-side endpoints and features (the primary pipeline).
- [`../api/bulk_datasets.md`](../api/bulk_datasets.md) — bulk-download catalog.
- [`../api/rate_limits.md`](../api/rate_limits.md) — quota buckets and concurrency limits.
- [`../scope/prediction_scope.md`](../scope/prediction_scope.md) — high-level model scope and what counts as in-scope features.
- `config/patents/event_codes.yaml` — the operational code→category lookup driving the family-count features.
