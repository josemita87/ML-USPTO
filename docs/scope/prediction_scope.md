# Prediction Task Scope

> Statute and rule citations in this doc (`§ 102`, `§ 103`, `§ 112`, `§ 325(d)`, `§ 315(b)`, etc.) and patent-law terms (Fintiv, Sotera, POSITA, RPI) are defined in plain English in `ptab_scope_and_terminology.md` §4.

This document fixes the prediction task, the eligibility rule for features, and the system-level consequences. It is the single source of truth on **what we are predicting** and **what we are allowed to use to predict it**. Other docs (endpoint maps, rate limits, feature catalogs) remain authoritative on *how* the data is fetched, but this doc takes precedence on *what counts as a valid feature for this project*.

---

## 1. Task definition

**Question.** At the moment an IPR petition is filed, what is the probability that the patent will be cancelled?

**Operationally.** Given a petition filed at time T₀, predict whether the **terminating** Final Written Decision (the original FWD, or a FWD-on-remand if the original is vacated by CAFC) will hold *all challenged claims* unpatentable.

**Single class boundary.** No staged prediction (i.e. no separate "institution decision" model followed by an "outcome given institution" model). One model. One decision. One label per petition.

## 2. Prediction time

T₀ = `trialMetaData.petitionFilingDate`, the day the petition is filed and the proceeding is created.

All features must be **observable at T₀ or earlier**. This is the single inviolable constraint of the project; everything in §4 derives from it.

## 3. Target variable

| Terminating outcome | Label | Source |
|---|---|---|
| FWD — all challenged claims unpatentable | **1** | Terminating decision's outcome categorisation |
| FWD — any challenged claim survives | 0 | Same |
| Institution denied | 0 | `trialStatusCategory == "Institution Denied"` |
| Discretionary denial | 0 | `trialStatusCategory == "Discretionary Denial"` |
| Terminated – settled | 0 | `trialStatusCategory == "Terminated-Settled"` |
| Terminated – procedural | 0 | `trialStatusCategory == "Terminated"` (joinder, improper filing, etc.) |
| Trial still pending | excluded | `trialStatusCategory == "Trial Instituted"` or similar in-progress states |

The terminating decision is identified per trial as the FWD with the latest `decisionIssueDate` (so on a remand path, the remand FWD wins; the vacated original is not the label source).

**Settlement coding.** Settled trials are 0: claims are not formally cancelled. Flag this as a sensitivity test (§7) — re-label settled = 1 and check whether the model meaningfully changes.

**Partial-cancellation coding.** Some-but-not-all claims unpatentable → 0. The task is "patent cancelled", not "petitioner partially won". A future regression-style follow-up can target fraction-of-claims-cancelled.

**Pending-appeal coding.** If the original FWD is currently on appeal at CAFC and no remand FWD exists yet, exclude the trial from training rather than label from the (potentially-to-be-vacated) original FWD.

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
- `institutionDecisionDate`, `terminationDate`, `latestDecisionDate`.
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
| Per-trial document index for all ~18K trials | **Cut.** Only the petition paper is needed, and it's always within the 25-record cap of the documents endpoint. |
| Decision PDFs for Fintiv / dispositive-factor extraction (~25K files, 5–15 GB) | **Cut as feature source.** No decision text is read. |
| `decisionData` features (`statuteAndRuleBag`, `issueTypeBag`, `appealOutcomeCategory`) | **Demoted to label-only.** One pull per trial of `trialOutcomeCategory` + `decisionIssueDate` is sufficient. |
| Expert declaration parsing (Ex 1003) | **Cut as feature source.** Cheap proxies (declaration filed? max ¶ cited?) extracted from the petition itself — see §8.1. |
| Parallel-lawsuit exhibit parsing (Ex 1031–1034, 1041–1043) | **Cut as feature source.** Petition §IV / §VI.B restate the categorical facts (forum, dates, judge, co-defendants) — see §8.1. |
| Domain-expert Fintiv-rating spreadsheet | **Optional.** Useful for understanding why some petitions get discretionary-denied — informs petition-side feature engineering — but not used as a feature directly. |
| Per-trial document indices for petition / POPR / FWD enumeration | **Cut.** Petition retrieval only. |

### 5.2 The petition PDF is the only text-feature source

We open exactly one PDF per trial: the petition (Paper 3), plus optionally a small ranking-notice PDF (Paper 2, ~140 KB, only present when ≥2 petitions stack against the same patent). Per §8.1 every other admissible PDF is skipped. Net text-storage budget: **~4–5 GB corpus-wide** (~18K petitions × ~4 MB ≈ 70 GB upper bound; in practice many petitions are smaller).

Retrieval path:
1. **Trial inventory + label** — `POST /trials/proceedings/search` paginated, filtered to `trialMetaData.trialTypeCode: "IPR"`. One row per trial with live `trialStatusCategory`, `terminationDate`, etc.
2. **Per-trial petition row** — for each trial, `POST /trials/documents/search` with `filters: [{name: "trialNumber", value: [<trial>]}]`, paginated. Run the petition picker (see `../api/proceedings.md` "Petition coverage and the category-taxonomy drift") to identify the petition row from its document-list. The picker handles both the new `documentCategory: "PETITION"` taxonomy and the legacy `Paper` catch-all bucket. Each picked row carries the petition document's `documentData` (including `fileDownloadURI`) plus a **header frozen at T₀** (`trialMetaData`, `patentOwnerData`, `regularPetitionerData`) — a leakage-discipline win because post-T₀ fields like `terminationDate` are simply absent on petition rows.
3. **PDF download** — `GET <documentData.fileDownloadURI>` from the picked row.

### 5.3 Patent file wrapper bulk products become first-tier

The order in `../api/bulk_datasets.md §4` becomes the actual ingestion order — these are no longer "enrichment", they're the structured-feature backbone:

1. `PASDL` / `PASYR` — assignments (NPE detection, ownership chain at T₀).
2. `PTMNFEE2` — maintenance fees through T₀ (commercial-importance proxy).
3. `PTFWPRE` — file wrapper bulk (events, PTA, continuity).
4. `OACT` — office actions, deferred until §3.5 features are exhausted.

Download-once-cache-locally discipline applies (the 20-per-file-per-year bulk cap is hard).

### 5.4 Cost model (canonical for this scope)

The corpus-wide `documentCategory: "PETITION"` filter only catches ~6K of the ~18K IPR trials, because pre-2022 trials use the legacy `Paper` category (which is also a catch-all for all procedural papers). The actually-correct ingestion path is **one per-trial documents/search call** (validated empirically at 100% recall — see `../api/proceedings.md`).

| Pass | Calls / size | Bucket | Wall time |
|---|---:|---|---|
| Trial inventory + label — `POST /trials/proceedings/search` paginated | ~200 calls | Metadata (5M/wk) | ~30 s |
| Per-trial documents enumeration + petition picker — `POST /trials/documents/search` filtered by `trialNumber` | ~18K calls (1 per trial; most trials fit in one page of 100 docs) | Metadata (5M/wk) | ~30 min serial, less with parallelism |
| Optional FWD label cross-check — `POST /trials/documents/search` filtered to `documentCategory: "FINAL"` | ~20 calls | Metadata (5M/wk) | ~5 s |
| Petition PDF downloads | ~18K, **~4–5 GB** | File-Wrapper Documents (1.2M/wk) | ~30 min |
| Bulk: `PASDL` + `PTMNFEE2` + `PTFWPRE` | 3 products, ~70 GB | Bulk (20/file/yr) | one-time |

Total live-API wall-clock: **~60 min**, split roughly evenly between per-trial document enumeration and PDF downloads. Both fit comfortably inside the 5M/wk metadata bucket and the 1.2M/wk file-wrapper bucket. Storage dominated by `PTFWPRE` (~63 GB); petition PDFs are ~4–5 GB. **This table is canonical for this project — `../api/rate_limits.md` §2 and `../api/api_feature_map.md` §5 defer to it.**

### 5.5 Train / test symmetry

Every feature is observable at T₀, so training and inference paths are identical — one snapshot per petition, same schema. No two-stage cascades, no missing-at-inference handling. Cross-validation should be **time-based**: train on petitions filed before a cutoff, test on later petitions. This mimics deployment and prevents the model from learning patterns that wouldn't generalise forward.

### 5.6 Leakage discipline as a first-class concern

Each feature column in the modelling matrix should declare its provenance: which source produced it and what date that source is anchored to relative to T₀. The pipeline should reject columns that can't prove they're ≤ T₀.

The two metadata sources differ on how leakage-resistant they are out of the box:

- **`documents/search` filtered to PETITION rows** — header is **frozen at T₀**. Post-T₀ mutable fields like `terminationDate` and `institutionDecisionDate` are simply absent on these rows (verified empirically 2026-04-26). Use this as the primary feature snapshot; the API itself enforces the leakage discipline.
- **`proceedings/search`** — header is **live**. The same JSON object mixes admissible fields (`petitionFilingDate`, `accordedFilingDate`, static patent / party blocks) with post-T₀ fields (`trialStatusCategory`, `terminationDate`, `institutionDecisionDate`, `latestDecisionDate`). Use only for the label, never for features without an explicit field-level allowlist.

## 6. Document map (what each doc owns)

| Asset | Role under this scope |
|---|---|
| `prediction_scope.md` (this doc) | **Authoritative on what we predict and what we may use.** §4 leakage rule + §8 modeling assumptions are binding. |
| `../api/api_feature_map.md` | Endpoint surface — *which endpoint returns which field*. Updated §2 marks each field's status under this scope. Defers to this doc on inclusion. |
| `ptab_scope_and_terminology.md` | Patent-law glossary (statutes, rules, Fintiv lifecycle). §4 statute glossary + §5 Fintiv lifecycle are the canonical references for those concepts. |
| `../api/bulk_datasets.md` | Bulk-product catalog and ingestion order — primary structured-feature source. |
| `../features/patent_file_wrapper_features.md` | Patent-side feature catalog (joins on `patentNumber`). |
| `../api/rate_limits.md` | Quota constraints. Cost model defers to §5.4 here. |
| `../features/admissible_documents_analysis.md` | Per-document analysis for `IPR2022-01002`; defines the 54-feature petition-derived catalog and tier demotions. |
| `domain_notes.md` | Legal-domain *why* behind features. Reconciled with binary-classification scope and petition-only feature policy. |
| `../examples/ipr_lifecycle_case_study.md` | Full IPR lifecycle (1,430 days, 145 papers). §9 explicitly lists Phases 3–8 as out-of-scope as feature sources. Orientation only. |
| `../api/proceedings.md` | Live-vs-frozen distinction between the proceedings and documents endpoints (empirical, 2026-04-26). Proceedings = label source; documents/search-petition = T₀-frozen feature snapshot. |
| `src/ml_uspto/parse/admissibility.py` | T₀ leakage filter — partitions a trial's documents by `documentFilingDate ≤ T₀`. |
| `src/ml_uspto/clients/uspto.py` | API client. Proceedings POST + documents POST + decisions POST are all part of the active surface — distinct roles per `../api/proceedings.md`. |

## 7. Open questions and sensitivity tests

- **Settlement coding.** Re-train with `Terminated-Settled` labelled 1; report whether feature importance and AUC shift materially. Decides whether "petition pressure" should be modelled separately from "patent cancelled".
- **Partial cancellation.** After the binary baseline lands, fit a regression on fraction-of-challenged-claims-cancelled.
- **Joinder treatment.** Quantify the joinder share of the corpus; decide whether to exclude or include with an `is_joinder` indicator.
- **Pre / post-2025 regime split.** New discretionary-denial framework took effect June 2025. Either include a regime indicator or fit two regime-specific models and compare.
- **Time-correct base rates.** Implementation discipline — art-unit / tech-center base-rate features must use only trials with terminating FWDs before T₀, not the full corpus.
- **Petition-text feature granularity.** Begin with bag-of-words / TF-IDF + structural counts (claims challenged, prior-art references, ground count). Defer transformer-based embeddings until the structured baseline is established.

## 8. Modeling assumptions and known limitations

These assumptions narrow the scope further than §1–§7 require, in exchange for a much simpler ingestion pipeline. State them upfront when presenting model results so reviewers understand what we *chose* to ignore and what we *had* to ignore.

### 8.1 Petition is the only PDF we read

We open exactly one PDF per trial: the petition itself (Paper 3). Optionally a second one — the petitioner's ranking notice — when present. **All other admissible documents are skipped**, even though the leakage rule (§4) would allow reading them.

Specifically skipped:
- **Expert declaration (Ex 1003).** Its content is expanded prose of what the petition already says; the only useful signals (declaration filed? cited paragraph count?) are derivable from the petition's exhibit list and `EX-1003, ¶N` citations.
- **Parallel-lawsuit exhibits (Ex 1031–1034, 1041–1043 in the example trial).** Court captions, trial dates, scheduling information are restated by the petition in §IV (Discretionary Considerations) and §VI.B (Related Matters). The exhibits provide verification, not new categorical features.
- **Prior-art exhibits (Ex 1004–1030 etc.).** Treated as a count-and-classify list parsed from the petition's exhibit-list section; the prior-art PDFs themselves are never opened.
- **Power of Attorney, the challenged patent itself, the patent's prosecution history.** Each is either redundant (POA → counsel info already in petition §VI.C) or duplicated by bulk products (`PTFWPRE`).

Net text-ingestion drops from the ~50–90 GB worst-case in §5.4 to **~4–5 GB** corpus-wide (petition-only).

See `../features/admissible_documents_analysis.md` for the per-document analysis that informed each demotion.

### 8.2 We extract Fintiv advocacy, not adjudication

Fintiv (the rule for refusing IPRs that overlap with parallel court trials) is the most predictive set of features and also the one with the cleanest leakage problem. Two cleaner sources exist (POPR, Institution Decision) but both have `documentFilingDate > T₀` and are excluded.

What we extract is the **petitioner's preemptive framing in petition §IV** — sales pitch, not ruling. Implications:

- The *thoroughness* of the §IV discussion is itself a feature. A petition that hand-waves Fintiv signals weakness independent of whether its arguments are correct.
- The Sotera stipulation is the one piece of §IV that's a binding commitment rather than rhetoric, and is therefore unusually high-signal.
- Ground-truth Fintiv labels (the PTAB's actual factor-by-factor ruling) live in the Institution Decision and are accessible only for evaluation/sanity-check, never as features.

See `ptab_scope_and_terminology.md` §5 for the full Fintiv lifecycle.

### 8.3 Lower-precision parallel-lawsuit data

Because we don't open the parallel-lawsuit exhibits, we lose precision on a few derivable features:

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
