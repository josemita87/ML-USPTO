# Prediction Task Scope

> Statute and rule citations in this doc (`§ 102`, `§ 103`, `§ 112`, `§ 325(d)`, `§ 315(b)`, etc.) and patent-law terms (Fintiv, Sotera, POSITA, RPI) are defined in plain English in `ptab_scope_and_terminology.md` §4.

This document fixes the prediction task, the eligibility rule for features, and the system-level consequences. It is the single source of truth on **what we are predicting** and **what we are allowed to use to predict it**. Other docs (endpoint maps, rate limits, feature catalogs) remain authoritative on *how* the data is fetched, but this doc takes precedence on *what counts as a valid feature for this project*.

---

## 1. Task definition

**Question.** At the moment an IPR petition is filed, what is the probability that the patent will be cancelled?

**Operationally.** Given a petition filed at time T₀, predict whether the **original** Final Written Decision will hold *all challenged claims* unpatentable. On-remand and rehearing FWDs are deliberately excluded from the label-source set — see §3.1.

**Single class boundary.** No staged prediction (i.e. no separate "institution decision" model followed by an "outcome given institution" model). One model. One decision. One label per petition.

## 2. Prediction time

T₀ = `trialMetaData.petitionFilingDate`, the day the petition is filed and the proceeding is created.

All features must be **observable at T₀ or earlier**. This is the single inviolable constraint of the project; everything in §4 derives from it.

## 3. Target variable

| Terminating outcome | Label | Source |
|---|---|---|
| FWD — all challenged claims unpatentable | **1** | Original FWD's PDF cover page (`Determining All Challenged Claims Unpatentable`) — see §3.1. The structured `decisionData.trialOutcomeCategory` is empty for IPRs (every FWD comes back as the bare string `Final Written Decision`, verified 2026-04-29 cold-run, 0/2,180 FWDs match any granular outcome), so the granular ruling has to come from the PDF. |
| FWD — any challenged claim survives | 0 | Same — PDF cover says `No`/`Some Challenged Claims Unpatentable` or enumerates a subset. |
| Institution denied | 0 | `trialStatusCategory == "Institution Denied"` |
| Discretionary denial | 0 | `trialStatusCategory == "Discretionary Denial"` |
| Terminated – settled | 0 | `trialStatusCategory == "Terminated-Settled"` |
| Terminated – procedural | 0 | `trialStatusCategory == "Terminated"` (joinder, improper filing, etc.) |
| Terminated – dismissed | 0 | `trialStatusCategory == "Terminated-Dismissed"` (procedural) |
| Terminated – adverse judgment | **1** | `trialStatusCategory == "Terminated-Adverse Judgment"` — patent owner concession under 37 CFR § 42.73(b); claims are cancelled. Configured in `config/labels.yaml::non_fwd_label_1_statuses`. ⚠ Subject to domain-expert review; revisit alongside the §7 settlement-coding sensitivity test if the "what counts as cancelled?" definition shifts. |
| Trial still pending | excluded | `trialStatusCategory ∈ {"Pending", "Pending Director Review", "Trial Instituted"}` — all three vocabularies are now in `config/labels.yaml::pending_statuses`. |

> ⚠ **`trialStatusCategory` does not carry the FWD verdict.** For FWD-reaching trials the proceedings endpoint stops at `Final Written Decision` or `Final Written Decision - Appealed` — neither value distinguishes "all claims unpatentable" from "mixed" or "all patentable". And the decisions endpoint isn't a structured fallback either: every IPR FWD's `decisionData.trialOutcomeCategory` is the bare string `Final Written Decision` (0/2,180 carry a granular outcome — verified 2026-04-29 cold-run). The verdict for FWD-reaching trials is recoverable only by parsing the PDF cover page. Empirically this affects ~38% of terminated trials (286 + 92 of 1000 IPR2022 trials sampled) — see `../api/proceedings.md` "The proceedings status stops at FWD reached".

The terminating decision is identified per trial as the **original** FWD; amendments are dropped (see §3.1).

**Settlement coding.** Settled trials are 0: claims are not formally cancelled. Flag this as a sensitivity test (§7) — re-label settled = 1 and check whether the model meaningfully changes.

**Partial-cancellation coding.** Some-but-not-all claims unpatentable → 0. This is a modeling choice tied to the petitioner-sweep framing — see §3.2 for the rationale. A future regression-style follow-up can target fraction-of-claims-cancelled.

**Pending-appeal coding.** If the original FWD is on appeal at CAFC, label from the original FWD anyway. We do not wait for the remand to land, and we do not switch label sources if the remand vacates part of the original — see §3.1 "Trade-off".

### 3.1 FWD scope: originals only

A single IPR trial can have multiple FWD-tagged decisions in the API. From the 2026-04-29 cold-run (2,180 FWD rows total):

| `documentTypeDescriptionText` | rows | % |
|---|---|---|
| `Final Written Decision:  original` | 2,076 | 95.2% |
| `Final Written Decision: On remand from the CAFC` | 67 | 3.1% |
| `Final written decision:  On remand` | 17 | 0.8% |
| `Final Written Decision:  rehearing` | 11 | 0.5% |
| `Final Written Decision: on Remand from the Director` | 9 | 0.4% |

For label construction we use **only the original FWD** per trial. The four amendment variants (on-remand-CAFC, on-remand, rehearing, on-remand-Director) are dropped at the parse layer; trials whose only FWD in the cache is an amendment remain label-unresolvable and are dropped from the training frame.

**Why originals only.** Amendment FWDs are partial rulings layered on top of the original. Their cover-page outcome refers to the *currently-litigated* claim subset (the claims that came back on remand, or that are addressed in the granted rehearing), **not** the originally-challenged set. Applying our cover-page regex to an amendment in isolation gives the wrong trial-level answer — empirical examples from the 2026-04-29 hand-label sample:
- `170155370` (on-remand-CAFC): cover says "Determining All Challenged Claims Unpatentable", body's V. ORDER lists only 4 claims (the remanded subset). The original FWD ruled on 20 claims.
- `171036280` (on-remand-Director): cover says "Determining Only Remanded Challenged Claim Unpatentable" — phrasing only used for amendments. Originally challenged 3–18; only one claim came back.
- `171062552` (on-remand-CAFC): cover says "Determining Proposed Substitute Claims … Unpatentable" — addresses the patent-owner's Motion-to-Amend substitute claims, not the originally-challenged claims at all.
- `170553849` (on-remand-CAFC): cover says "Determining Claim 22 Not Unpatentable" — single-claim reversal supplement. The other 21 originally-challenged claims' rulings stand from the original.

The rigorous alternative — layering original + amendments into a combined per-trial outcome — is real infrastructure work for a population that's <5% of FWDs. Out of project scope for v1.

**Trade-off.** Trials whose original FWD was *substantially* vacated on appeal get labelled per the (vacated) original ruling. We accept this. The amendment population (~104 of ~2,180 FWDs, 4.8%) further breaks down:
- *Partial reversals* (most common) don't move the binary label: original "all unpatentable" + remand "claim X not unpatentable" still has a survival → label was already 0 if there were other surviving claims, or moves 1→0 only if the original was "all unpatentable" with zero survivors and the remand resurrects exactly one.
- *Full reversals* do move the label, but are rare (CAFC reverses outright in roughly 5% of appeals it hears).

Net expected label noise from this policy: ≪1% of the FWD population. Acceptable for v1; revisit if the model is sensitive to it.

**Settlement-on-remand mislabels.** Some `Final Written Decision: On remand` rows are actually `TERMINATION` documents that the API mislabels as FWDs (e.g., parties settle while a remand is open — the cover says `TERMINATION Due to Settlement on Remand`, never `Determining ...`). The originals-only filter incidentally drops these — they're not real FWDs and shouldn't carry a label.

**Rehearing-denied mislabels.** Similarly, some `Final Written Decision: rehearing` rows are actually denials of a Patent-Owner request for rehearing of the original FWD (cover says `DECISION Denying Patent Owner's Request for Rehearing`, body's ORDER says only "request for rehearing is denied"). These are procedural denials, not new outcomes; the originals-only filter drops them as well.

### 3.2 Business framing: why the binary cut is all-or-nothing

The four cover-page phrasings split into three outcome classes — petitioner sweep, partial, patent-owner sweep — but the label collapses to two: `All Challenged Claims Unpatentable` → 1, everything else → 0. Calling out why, since "Some Challenged Claims Unpatentable" → 0 is the non-obvious case:

- **Petitioner-sweep framing.** An IPR is run to invalidate *the patent*. The asserted claims in the parallel district-court infringement suit are typically the same set the petition challenges — so a partial outcome (claims 1–3 cancelled, claims 4–9 surviving) usually leaves the patent owner with something to assert. The "win" the petitioner is buying is "all challenged claims unpatentable"; partial wins are closer to a draw than a win in business terms. The label answers *"will the petitioner sweep?"*, not *"will any claim be cancelled?"*.
- **Threshold stability.** "Some" is operationally fuzzy without per-claim adjudication: which claims, what fraction, were they independent or dependent, were they the asserted ones in litigation? The Board's cover-page language doesn't say. Collapsing "Some" → 0 sidesteps that and gives a label that's reproducible from the cover page alone.
- **Class balance.** Partial outcomes are roughly 5–10% of FWDs (`docs/api/api_feature_map.md` §3 / §3.1 manifest). A 3-class target (all / some / none) creates a tiny middle class with high variance — usually worse models than the binary cut.

**What this choice costs.** Patent-owner-side prediction. From a patent owner's perspective, even one cancelled claim can be a real loss. A model trained on this label is implicitly answering the *petitioner*'s question. The patent-owner question — *"will I walk away unscathed?"* — is the opposite binary cut (1 iff `No Challenged Claims Unpatentable`), and the same regex set distinguishes those three classes already. **Schema-additive escape hatch:** if a future use case wants the patent-owner cut or the 3-class target, expose `outcome ∈ {all_unpatentable, mixed, none_unpatentable}` alongside the binary `cancelled` — `parse.fwd_outcome.extract_outcome` would need to return the per-pattern label rather than collapse to 0/1. The cover-page regex already discriminates the three classes; only the label-collapse step throws information away.

So `Some` → 0 is **defensible and currently canonical**, but worth being explicit that it's a scope decision tied to the question this project is built to answer, not a fact about the data.

## 4. The leakage rule

A feature is admissible iff every component of it was observable on or before T₀.

```mermaid
flowchart LR
    subgraph Pre-T₀["BEFORE T₀ — admissible"]
        FW[Patent file<br/>wrapper]
        ASS[Assignments<br/>NPE detection]
        MF[Maintenance<br/>fees]
        PET[The petition itself<br/>+ co-filed exhibits]
        MACRO[Macro indicators<br/>policy regime, quarter]
        BR[Time-correct<br/>base rates*]
    end
    T0((T₀<br/>petition<br/>filed))
    subgraph Post-T₀["AFTER T₀ — leaks, excluded"]
        POPR[POPR]
        ID[Institution<br/>decision]
        POR[POR]
        REPLY[Reply / Sur-Reply]
        FWD[Final Written<br/>Decision]
        CAFC[CAFC outcome]
    end
    FW --> T0
    ASS --> T0
    MF --> T0
    PET --> T0
    MACRO --> T0
    BR --> T0
    T0 -.skip.-> POPR
    T0 -.skip.-> ID
    T0 -.skip.-> POR
    T0 -.skip.-> REPLY
    T0 -.skip.-> FWD
    T0 -.skip.-> CAFC
    FWD ==label only==> Y[Target<br/>variable]
```

*Base rates that aggregate over other trials' outcomes are admissible only when computed over trials with terminating FWDs strictly before T₀.

**Allowed sources** (all dated ≤ T₀):
- Patent file wrapper: bibliographic data, prosecution history (events, office actions, amendments, RCEs), claim text, CPC classifications, examiner identity, art unit, tech center, family / continuity tree.
- Maintenance-fee history.
- Patent assignment chain (NPE detection, ownership transitions).
- The petition itself: full text, structure, claims challenged, statute grounds asserted (102/103/112), prior-art references, exhibits, expert declarations, petitioner-side counsel and real party in interest.
- Patent owner counsel and real party in interest as listed in the petition.
- Macro indicators: PTAB policy regime, USPTO directorship, calendar quarter, trial-type code.
- **Time-correct base rates.** Art-unit and tech-center cancellation rates are admissible *only* when computed over trials whose terminating FWD was issued strictly before T₀ — to prevent leaking future trials' outcomes via summary statistics.

**Disallowed sources** (any of these leaks):
- Everything in `decisionData` for any decision in this trial.
- `institutionDecisionDate`, `terminationDate`, `latestDecisionDate`, `trialStatusCategory` — *from any source*. **Including the petition document row.** The `trialMetaData` block on a document row is the trial-level header denormalized at indexing time, refreshed on a separate cadence from the proceedings endpoint. It is NOT frozen at T₀ — for older or recently-touched trials it can carry the live trial state. See `../api/proceedings.md` "What we observed" for the empirical lag table. Treat document-row `trialMetaData` as off-limits; pull all label/state fields from `proceedings/search`.
- Preliminary Response (POPR), Patent Owner Response (POR), Petitioner Reply, Sur-Reply — text and metadata.
- Any paper with `documentFilingDate > T₀`. The exception is papers bundled *with* the petition (Power of Attorney, Mandatory Notice when co-filed, exhibits 1xxx) — these have `documentFilingDate == T₀` and are admissible.
- CAFC outcomes (`appealOutcomeCategory`, mandate dates).
- Fintiv-factor ratings, dispositive-factor labels, 325(d)-addressed flags — all of these come from the institution decision or FWD.

**Cases where exclusion is safer than careful handling:**
- **Joinder petitions** — they reference an existing trial whose record contains post-T₀ data; risk of cross-trial leakage. Train on stand-alone petitions only; identify joinder via petition-name patterns and procedural orders.
- **Repeat petitions against the same patent.** Keep the earliest petition per patent; later petitions' features are partially conditioned on the first petition's trajectory.

## 5. System implications

### 5.1 The pipeline shrinks

| Previously planned pass | Status under this scope |
|---|---|
| Per-trial document index for all ~18K trials | **Cut.** A corpus-wide `PETITION` / `Paper` scan identifies the petition row and stores its `documentData.*` fields. |
| Decision PDFs for Fintiv / dispositive-factor extraction (~25K files, 5–15 GB) | **Cut as feature source.** Decision text is read only for FWD label fallback when status + title cannot resolve the binary outcome. |
| `decisionData` features (`statuteAndRuleBag`, `issueTypeBag`, `appealOutcomeCategory`) | **Demoted to label/debug only.** Decisions identify the original FWD and its PDF, but IPR `trialOutcomeCategory` is not granular enough for the binary verdict; unresolved FWD labels use title / cover-page text parsing. |
| Expert declaration parsing (Ex 1003) | **Cut as feature source.** Cheap proxies (declaration filed? max ¶ cited?) extracted from the petition itself — see §8.1. |
| Parallel-lawsuit exhibit parsing (Ex 1031–1034, 1041–1043) | **Cut as feature source.** Petition §IV / §VI.B restate the categorical facts (forum, dates, judge, co-defendants) — see §8.1. |
| Domain-expert Fintiv-rating spreadsheet | **Optional.** Useful for understanding why some petitions get discretionary-denied — informs petition-side feature engineering — but not used as a feature directly. |
| Per-trial document indices for petition / POPR / FWD enumeration | **Cut.** Petition retrieval only. |

### 5.2 Petition PDF text features deferred to v2

**v1 features are metadata-only.** No petition PDF is downloaded or parsed. The petition row's `documentData.fileDownloadURI` is captured in `petitions.parquet` but not dereferenced. Tier 1 (structural / volumetric) and Tier 2 (statutory / procedural posture) text features are deferred until the metadata-only baseline is established.

The current PDF exception is label-only: unresolved original-FWD decisions are fetched, converted to text, and cached under `Stage.DECISION_TEXTS` so the cover-page outcome can be parsed. That text never enters the feature matrix.

Retrieval path used in v1:
1. **Trial inventory + label** — `POST /trials/proceedings/search` paginated, filtered to `trialMetaData.trialTypeCode: "IPR"`. One row per trial with live `trialStatusCategory`, `terminationDate`, etc.
2. **Corpus-wide petition row** — `POST /trials/documents/search` filtered to `documentData.documentCategory IN ["PETITION", "Paper"]`, paginated, then grouped by `trialNumber` and run through `pick_petition()` (see `../api/proceedings.md` "Petition coverage and the category-taxonomy drift"). Use **only `documentData.*`** from the picked row — `fileDownloadURI` (stored for v2), `documentFilingDate` (= T₀), `documentNumber`, `documentTitleText`. The `trialMetaData` block on this row is **not** frozen at T₀ (refer to `../api/proceedings.md` "What we observed"); pull all trial / patent / party features from the proceedings row in step 1.

> **v2 (deferred): step 3 — `GET <documentData.fileDownloadURI>` per petition + `pdfplumber` extraction → Tier 1/2 features.**

### 5.3 Patent file wrapper API is first-tier

The structured-feature backbone comes from the live `/applications/search`
file-wrapper API, batched by unique `applicationNumberText` and cached under
`Stage.PATENTS`. The parser keeps scalar metadata and parallel arrays for
events / assignments / CPC codes; `features.transforms.build_features` applies
the T₀ filter and aggregation after the trial join.

Bulk products in `../api/bulk_datasets.md` remain useful future scaling inputs,
but they are not the current ingestion path.

Current first-tier patent-side families:

1. Bibliographic and technology metadata: filing date, CPC codes, entity size, inventor count, attorneys.
2. PTA and continuity: PTA quantities, parent-application count.
3. Pre-T₀ prosecution events: event-family counts, office actions, prosecution span.
4. Pre-T₀ assignments: assignment counts, distinct assignee count, days since last assignment.

### 5.4 Cost model (canonical for this scope)

The corpus-wide `documentData.documentCategory: "PETITION"` filter only catches ~6K of the ~18K IPR trials, because pre-2022 trials use the legacy `Paper` category (which is also a catch-all for all procedural papers). The canonical ingestion path is a **single corpus-wide scan filtered to `documentData.documentCategory IN ["PETITION", "Paper"]`** with the picker applied per trial — validated empirically at 98.7% recall, 0 false positives across 239 stratified trials (see `../api/proceedings.md`). The earlier per-trial draft (18K calls) is superseded by the corpus-wide scan (~3.2K calls) — same coverage, ~6× fewer requests.

**v1 cost model (metadata-only):**

| Pass | Calls / size | Bucket | Wall time |
|---|---:|---|---|
| Trial inventory + label — `POST /trials/proceedings/search` paginated | ~200 calls | Metadata (5M/wk) | ~30 s |
| Corpus-wide petition scan — `POST /trials/documents/search` filtered to `documentData.documentCategory IN ["PETITION", "Paper"]`, paginated 100/page | ~3.2K calls (~323K rows fetched, ~17.8K kept after `pick_petition()`) | Metadata (5M/wk) | ~25–30 min serial (rate-limit-bound) |
| FWD decision inventory — `POST /trials/decisions/search` paginated | ~200 calls | Metadata (5M/wk) | ~30 s |
| FWD text backfill for label fallback | ~1.1K PDFs cold; ~30–50 weekly deltas | File Wrapper Documents (1.2M/wk) | ~1 h cold; <5 min weekly |
| Patent file-wrapper enrichment — batched `POST /applications/search` by unique app | ~120 calls for the current ~11.8K-app corpus at page size 100 | Patent metadata bucket | ~3–4 h including response-size bisection / retries |

> **v2 (deferred): petition PDF downloads** — ~18K calls, ~70 GB raw / ~2 GB extracted text, File-Wrapper Documents bucket, ~10 h serial.

Total v1 live-API wall-clock: **~4–5 h on a cold run with FWD-text backfill**, dominated by patent file-wrapper enrichment. Metadata calls sit comfortably inside the 5M/wk cap; the binding constraint is wall-clock from burst=1 serialization and large patent-wrapper responses, not quota. Storage: raw JSON cache + FWD text blobs + feature parquets, all on local FS (and replicable to S3 within free-tier limits). Empirical probe (2026-04-28): the petition-scan stage runs at ~0.85 s/page including the ~5 s 429 backoffs that happen every ~250 pages, so ~3.2K pages = ~25–30 min wall-clock. **This table is canonical for v1 — `../api/rate_limits.md` §2 and `../api/api_feature_map.md` §5 defer to it.**

### 5.5 Train / test symmetry

Every feature is observable at T₀, so training and inference paths are identical — one snapshot per petition, same schema. No two-stage cascades, no missing-at-inference handling. Cross-validation should be **time-based**: train on petitions filed before a cutoff, test on later petitions. This mimics deployment and prevents the model from learning patterns that wouldn't generalise forward.

### 5.6 Leakage discipline as a first-class concern

Each feature column in the modelling matrix should declare its provenance: which source produced it and what date that source is anchored to relative to T₀. The pipeline should reject columns that can't prove they're ≤ T₀.

Neither endpoint enforces leakage discipline for us — the API has no T₀-frozen view. Both endpoints return the trial header as denormalized state at indexing time:

- **`proceedings/search`** — refreshed within days of every trial event. The JSON object mixes admissible fields (`petitionFilingDate`, `accordedFilingDate`, static patent / party blocks) with post-T₀ fields (`trialStatusCategory`, `terminationDate`, `institutionDecisionDate`, `latestDecisionDate`). Use for the label and for an **explicit field-level allowlist** of static / pre-T₀ fields; never for the post-T₀ fields above.
- **`documents/search` (petition row)** — `trialMetaData` is the same trial-level header but refreshed by a *separate*, slower indexer. For older / recently-touched trials the petition row carries fully post-T₀ status and dates (e.g., IPR2022-01002's petition row reads `Final Written Decision - Appealed`). Earlier drafts of this scope claimed this row was "frozen at T₀" — that was wrong, based on a 6-trial sample of recent trials whose documents-side lag *happened* to keep the stamp near T₀. **Use only `documentData.*` from the petition row** (`fileDownloadURI`, `documentFilingDate`, `documentNumber`, `documentTitleText`); ignore `trialMetaData`. See `../api/proceedings.md` "What we observed" for the empirical lag table.

## 6. Document map (what each doc owns)

| Asset | Role under this scope |
|---|---|
| `prediction_scope.md` (this doc) | **Authoritative on what we predict and what we may use.** §4 leakage rule + §8 modeling assumptions are binding. |
| `../api/api_feature_map.md` | Endpoint surface — *which endpoint returns which field*. Updated §2 marks each field's status under this scope. Defers to this doc on inclusion. |
| `ptab_scope_and_terminology.md` | Patent-law glossary (statutes, rules, Fintiv lifecycle). §4 statute glossary + §5 Fintiv lifecycle are the canonical references for those concepts. |
| `../api/bulk_datasets.md` | Bulk-product catalog for future scaling; not the current v1 feature source. |
| `../features/patent_file_wrapper_features.md` | Patent-side feature catalog (joins on `patentNumber`). |
| `../api/rate_limits.md` | Quota constraints. Cost model defers to §5.4 here. |
| `../features/admissible_documents_analysis.md` | Per-document analysis for `IPR2022-01002`; defines the 54-feature petition-derived catalog and tier demotions. |
| `domain_notes.md` | Legal-domain *why* behind features. Reconciled with binary-classification scope and petition-only feature policy. |
| `../examples/ipr_lifecycle_case_study.md` | Full IPR lifecycle (1,430 days, 145 papers). §9 explicitly lists Phases 3–8 as out-of-scope as feature sources. Orientation only. |
| `../api/proceedings.md` | Live-vs-frozen distinction between the proceedings and documents endpoints (empirical, 2026-04-26). Proceedings = label / static metadata source; documents/search petition rows contribute only `documentData.*`. |
| `src/ml_uspto/features/transforms.py` | Current feature builder. Applies T₀ filtering to patent event / assignment arrays after the trial join. |
| `src/ml_uspto/clients/uspto.py` | API client. Proceedings POST + documents POST + decisions POST + application search + authenticated PDF download are all part of the active surface — distinct roles per `../api/proceedings.md`. |

## 7. Open questions and sensitivity tests

- **Settlement coding.** Re-train with `Terminated-Settled` labelled 1; report whether feature importance and AUC shift materially. Decides whether "petition pressure" should be modelled separately from "patent cancelled".
- **Partial cancellation.** After the binary baseline lands, fit a regression on fraction-of-challenged-claims-cancelled.
- **Joinder treatment.** Quantify the joinder share of the corpus; decide whether to exclude or include with an `is_joinder` indicator.
- **Pre / post-2025 regime split.** New discretionary-denial framework took effect June 2025. Either include a regime indicator or fit two regime-specific models and compare.
- **Time-correct base rates.** Implementation discipline — art-unit / tech-center base-rate features must use only trials with terminating FWDs before T₀, not the full corpus.
- **Petition-text feature granularity (v2 scope).** v1 ships no petition-text features. v2 starts with bag-of-words / TF-IDF + Tier 1/2 structural counts (claims challenged, prior-art references, ground count). Transformer-based embeddings deferred behind that.

## 8. Modeling assumptions and known limitations

These assumptions narrow the scope further than §1–§7 require, in exchange for a much simpler ingestion pipeline. State them upfront when presenting model results so reviewers understand what we *chose* to ignore and what we *had* to ignore.

### 8.1 Petition-text PDF plan is deferred

The v2 petition-text plan would open exactly one feature PDF per trial: the petition itself (Paper 3). Optionally a second one — the petitioner's ranking notice — when present. **Current v1 does not open petition PDFs at all.** All other admissible documents are skipped, even though the leakage rule (§4) would allow reading them.

Specifically skipped:
- **Expert declaration (Ex 1003).** Its content is expanded prose of what the petition already says; the only useful signals (declaration filed? cited paragraph count?) are derivable from the petition's exhibit list and `EX-1003, ¶N` citations.
- **Parallel-lawsuit exhibits (Ex 1031–1034, 1041–1043 in the example trial).** Court captions, trial dates, scheduling information are restated by the petition in §IV (Discretionary Considerations) and §VI.B (Related Matters). The exhibits provide verification, not new categorical features.
- **Prior-art exhibits (Ex 1004–1030 etc.).** Treated as a count-and-classify list parsed from the petition's exhibit-list section; the prior-art PDFs themselves are never opened.
- **Power of Attorney, the challenged patent itself, the patent's prosecution history.** Each is either redundant (POA → counsel info already in petition §VI.C) or duplicated by bulk products (`PTFWPRE`).

When v2 lands, petition-only text ingestion drops from the ~50–90 GB worst-case to the petition corpus plus the small ranking-notice tail. In v1, the only text blobs are FWD label-fallback text.

See `../features/admissible_documents_analysis.md` for the per-document analysis that informed each demotion.

### 8.2 Future Fintiv extraction: advocacy, not adjudication

Fintiv (the rule for refusing IPRs that overlap with parallel court trials) is the most predictive set of features and also the one with the cleanest leakage problem. Two cleaner sources exist (POPR, Institution Decision) but both have `documentFilingDate > T₀` and are excluded.

What v2 should extract is the **petitioner's preemptive framing in petition §IV** — sales pitch, not ruling. Implications:

- The *thoroughness* of the §IV discussion is itself a feature. A petition that hand-waves Fintiv signals weakness independent of whether its arguments are correct.
- The Sotera stipulation is the one piece of §IV that's a binding commitment rather than rhetoric, and is therefore unusually high-signal.
- Ground-truth Fintiv labels (the PTAB's actual factor-by-factor ruling) live in the Institution Decision and are accessible only for evaluation/sanity-check, never as features.

See `ptab_scope_and_terminology.md` §5 for the full Fintiv lifecycle.

### 8.3 Lower-precision parallel-lawsuit data in v2

When v2 uses petition-only text and does not open the parallel-lawsuit exhibits, it loses precision on a few derivable features:

- District-court complaint date is reduced from "exact filing date" to "year" (extracted from case number `21-cv-00701` → 2021).
- Judge name is captured only when the petition explicitly states it; otherwise inferred from the district.
- Co-defendant counts come from the petition's Real Party in Interest list rather than the lawsuit's caption.

For the Fintiv signals that actually matter (jury date, FWD-vs-trial timing, Sotera stipulation), the petition restates them verbatim, so no precision loss.

### 8.4 Single-jurisdiction PTAB scope

Trial-type filter is `IPR` only (CBM sunset in 2020; PGR/INT/DER excluded for low volume or structural mismatch). See `ptab_scope_and_terminology.md` §2.

### 8.5 Time-aware evaluation is mandatory

Cross-validation must be **time-based** (train on petitions ≤ cutoff, test on later petitions), not random k-fold. Random splits would let the model see future petitions' contemporary conditions during training. Reported metrics should always include the train/test temporal cutoff.

---

## 9. References

- `README.md §6` — the earlier multi-priority feature plan; the T₀ rule in §4 supersedes the per-priority enumeration for any decision about feature inclusion.
- `../api/api_feature_map.md` — endpoint mapping (still authoritative for "which endpoint returns what").
- `../api/bulk_datasets.md` — bulk catalogue and ingestion ordering.
- `../features/patent_file_wrapper_features.md` — primary feature catalogue under this scope.
- `ptab_scope_and_terminology.md` — terminology and statute glossary; §5 covers Fintiv.
- `../api/rate_limits.md` — quota constraints; cost table in §5.4 here is the operational version for this scope.
- `../features/admissible_documents_analysis.md` — per-document feature plan and tier demotions.
- `../examples/ipr_lifecycle_case_study.md` — the full lifecycle this scope deliberately ignores past T₀.
- `domain_notes.md` — legal-domain context behind the petition-text feature ideas.
