# PTAB Scope and Terminology

Short reference for the PTAB concepts that show up in this project, and what is in/out of scope for the trial-outcome model. Complements `api_feature_map.md` (how to extract features) and `exploration/domain_notes.md` (why features matter).

---

## 1. Proceeding vs. decision vs. appeal

These three terms are often used interchangeably in casual conversation but sit at different levels in the PTAB data model.

| Term | What it is | Granularity | Where it lives in the API |
|---|---|---|---|
| **Proceeding** | The case itself — one trial challenging one patent. Has a trial number (e.g., `IPR2016-00001`), a type, parties, dates, and a final outcome. | 1 row per case | `/trials/proceedings/search`, `/trials/proceedings/{trial_number}` |
| **Decision** | A ruling *inside* a proceeding. A single proceeding produces several over its lifetime: institution decision (~month 6), procedural decisions, Final Written Decision / FWD (~month 18). | Many per proceeding | `/trials/decisions/search` |
| **Appeal** | Usually refers to **ex parte appeals** — appeals from an examiner's rejection during patent *prosecution* (before grant). Handled by PTAB but **not AIA trials**: no petitioner/patent-owner structure, no Fintiv, no institution gate. Separately, an FWD in an IPR can be appealed *out of* PTAB to the Federal Circuit (CAFC) — that is an appeal out of PTAB, not a PTAB proceeding. | N/A for this project | Out of scope — not modeled |

**Implication for the pipeline:**
- Proceedings = rows and the source of the target label.
- Decisions = where most institution-stage features (Fintiv, 325(d), dispositive factor) come from.
- Appeals = filter out; they are a different proceeding category entirely.

### CAFC appeals — label handling, not a filter

Appeals *from* an IPR Final Written Decision *to* the Federal Circuit (CAFC) are **not a separate proceeding type to exclude**. They are downstream events that happen after an FWD. In our probed data (2026-04-24) they show up as a dedicated status:

- `trialStatusCategory: "Final Written Decision - Appealed"` — **591 records** (~3% of the docket).

Treat these as FWDs for target-variable purposes. Whether the CAFC later affirms or reverses is orthogonal to the model's prediction target (the PTAB trial outcome). If we ever want to condition on the CAFC result, `decisionData.appealOutcomeCategory` on the decisions endpoint carries `"Affirmed"` / `"Reversed"` / `"Vacated/Remanded"` values.

---

## 2. Trial types and in-scope filter

The PTAB docket mixes several proceeding categories. The `trial_type` field separates them.

| `trial_type` | Name | Status | In scope? |
|---|---|---|---|
| `IPR` | Inter Partes Review | Active, primary workload | **Yes** — primary target |
| `PGR` | Post-Grant Review | Active, lower volume | Optional — structurally similar to IPR |
| `CBM` | Covered Business Method review | Sunset September 2020 | Optional — historical only |
| `INT` | Interference | Legacy (pre-AIA) | **No** |
| `DER` | Derivation | AIA replacement for interference; very rare | **No** |
| (ex parte appeals) | Prosecution appeals from examiner rejection | Active but different system | **No** |

### Interferences and derivations (why they are out)

- **Interferences** determined *who invented first* under the pre-AIA "first-to-invent" system. The America Invents Act (2013) switched the US to "first-inventor-to-file," eliminating the need for new interferences. A residual tail of pre-AIA cases still closes out at the PTAB, but no new ones are filed.
- **Derivation proceedings** are the AIA-era replacement — narrower, used to prove a later filer derived the invention from an earlier filer. Only a handful per year.
- Neither has the petitioner-vs-patent-owner institution gate, Fintiv analysis, or 325(d) discretionary denial that the model relies on. They share almost no features with IPRs.

### Recommended filter

When pulling proceedings for training, restrict to `trial_type IN ('IPR')` as the default, with `PGR` as an opt-in expansion. Exclude `INT`, `DER`, CBM (unless doing historical analysis), and any ex parte appeal records that surface through adjacent endpoints.

---

## 3. Naming collision: "Petition Decision Search" is NOT PTAB

The ODP catalog exposes two API families that both use the word "decisions." They are completely distinct — easy trap when browsing the catalog.

| Family | URL path | Response bag | Deciding body | In scope? |
|---|---|---|---|---|
| **PTAB Trials — Decisions** | `/api/v1/patent/trials/decisions/*` | `patentTrialDocumentDataBag` | PTAB judges (AIA trial) | **Yes — core feature source** |
| **Petition Decision Search** | `/api/v1/petition/decisions/*` | `petitionDecisionDataBag` | USPTO Office of Petitions | **No — prosecution-procedural, unrelated** |

Telltale field on a record: `finalDecidingOfficeName: "OFFICE OF PETITIONS"` → wrong corpus. `trialNumber: "IPR…"` → right corpus.

### What "prosecution-procedural" means

The `/petition/decisions/*` corpus (~7,393 records, probed 2026-04-24) is dominated by Office of Petitions rulings on **procedural** matters during patent **prosecution** — i.e., the examination phase *before* a patent issues:

- **Prosecution** = the examination phase (filing → office actions → allowance/abandonment → grant). Ends before the patent exists; IPRs happen long after prosecution ends.
- **Procedural** (vs. substantive) = about *how* the case is handled (deadlines, fees, forms, case assignment) rather than *whether the invention is patentable* (novelty, obviousness, claim scope).

Top issue types in the corpus (all procedural):
- Patent Prosecution Highway requests (5,957 — 81%)
- Patent Term Adjustment disputes (~1,100)
- Withdrawal of abandonment (75), Waiver or suspension of a rule (80), Refund (26), Review of Technology Center (~200)

### Why this corpus is unusable for our model

1. **Wrong lifecycle.** Events happen before the patent is granted; our model starts at the IPR petition, which happens years after grant.
2. **Wrong dimension.** Procedural process mechanics, not substantive patent-strength signals. Patent strength is what predicts IPR outcomes.
3. **Tiny overlap.** 7K total records across all US patent applications ever. Most IPR'd patents never had an Office of Petitions decision.
4. **Redundant where it does have signal.** Any weak correlates of contentious prosecution (PTA disputes, abandonment revivals) are better captured by `OACT` office actions, `eventDataBag`, and `patentTermAdjustmentData`.

Skip this corpus. If it surfaces in future catalog browsing, the `finalDecidingOfficeName` check is the quickest way to confirm it's not what we want.
