# Domain Notes — IPR Outcome Prediction

Insights gathered from conversations with a practicing patent attorney (partner at a major IP firm, ~10 years experience in PTAB proceedings).

> **Scope reconciliation.** This doc captures the legal-domain *why* behind features. The authoritative *what / when / what's allowed* lives in `prediction_scope.md`. The class definition is now fixed as **binary** (FWD-all-claims-unpatentable = 1, everything else = 0; settled = 0; pending = excluded — see scope §3). Several features described below as "extractable from petition / preliminary response" are **petition-only** under the scope's leakage rule (§4) — POPRs are filed ~3 months after T₀ and excluded.

## Target Variable

**IPR trial outcome — exclusively.** Binary classification: did the terminating Final Written Decision hold all challenged claims unpatentable? See `prediction_scope.md` §3 for full label coding.

The institution decision is **not** a prediction target — this is a deliberate shift from the original framing. Institution-stage signals (Fintiv, 325(d), Sotera) remain central, but as *features* that help predict the downstream outcome. A petition that fails institution is effectively a "claims survive" outcome for the patent owner, so the institution gate is absorbed into the trial-outcome label.

## High-Signal Features

### Binary Flags (extractable from petition only — POPR is post-T₀)

| Feature | Description | Scope status |
|---------|-------------|---|
| Fintiv addressed | Whether the petition's §IV walks the six Fintiv factors | **In scope.** Petition §IV header detection. |
| 325(d) addressed | Whether the petition discusses prior-PTO-consideration of the asserted art | **In scope.** Regex on `§ 325(d)` in petition. |
| Sotera stipulation | Whether the petitioner committed not to raise the same invalidity arguments in district court | **In scope.** Extractable from petition §IV.4 (corrects earlier "external data needed" framing — see `ptab_scope_and_terminology.md` §5). |

### Fintiv Sub-Factors (6 factors, ordinal scale)

> **Scope note.** Judge-issued ratings live in the Institution Decision (post-T₀, excluded as a feature per scope §4). What we extract is the petitioner's **own framing of the six factors in petition §IV** — advocacy, not adjudication. See `ptab_scope_and_terminology.md` §5 for the full lifecycle and the six-factor extraction targets.

Judges rate each factor using consistent, predictable phrasing:
1. "heavily favors" institution
2. "favors" institution
3. "neutral"
4. "weighs against" institution
5. "heavily weighs against" institution

Decision documents have **explicit headings** per factor ("factor one", "factor two", etc.), making extraction straightforward via term search or LLM classification — useful for **ground-truth Fintiv labels in evaluation**, not as features.

The key practitioner question: **which factor is dispositive** — i.e., which one actually drives the outcome in a given case.

### Structural Features from Petition Documents

- Page count of the discretionary denial section (~2-3 pages in an ~80-100 page petition)
- Procedural section (Sec. 314(a), 314(d) arguments) vs. substantive section (claim-by-claim prior art analysis)
- Number of prior art references cited
- Technology type / domain as a categorical variable

### Metadata Features (already in API)

- `technology_center` / `group_art_unit` — technology domain
- `grant_date` — patent age at time of petition
- Counsel identity — some firms or attorneys may correlate with outcomes
- Filing date — captures which policy regime was active

## Political Regime Cycles

Institution rates are heavily driven by the political appointment cycle of USPTO directors. Each new administration appoints a new director who shifts policy:

| Period | Policy Change | Effect |
|--------|--------------|--------|
| 2020 | Fintiv factors introduced (Trump 1st term) | Discretionary denials spike |
| Dec 2020 | Sotera stipulation practice emerges | Institution rates recover |
| 2022 | Director memo neutralizes Fintiv (Biden term) | Massive increase in institution rates |
| March 2025 | New memo reintroduces discretionary denial emphasis (Trump 2nd term) | Denials expected to spike again |

The practitioner's assessment: "March 2025 is exactly like March 2020." The expected cycle: policy change -> panic -> rate dip -> practitioner adaptation (new stipulations, new arguments) -> rate recovery -> eventual policy reversal.

**Temporal features are likely the strongest macro predictor** — which policy regime a petition was filed under.

## New Factors (Expected June 2025)

- A March 2025 memo introduces 5-6 new discretionary denial factors
- May replace or supplement the existing Fintiv factors — unclear as of May 2025
- New document types in the API: "petitioner discretionary brief", "patent owner discretionary brief"
- Same API data source, new metadata identifiers
- Decisions currently being issued still apply old Fintiv framework; new-format decisions expected starting June 2025

## Revenue Incentive

Each IPR petition generates ~$40-50K in filing fees that the USPTO retains regardless of outcome. This creates a structural incentive against denying too aggressively — the patent office will not shut down the venue entirely.

## Document Structure and Consistency

- IPR petitions have a regulated structure (37 CFR §42.104): mandatory notices §VI, grounds §I.B, claim construction §II, prior-art mapping §III, discretionary considerations §IV, certification footer §42.24. Section anchors are reliable parser landmarks.
- Two-part substance: procedural arguments (short, ~2–3 pages, §IV) and substantive arguments (bulk of document, §III).
- All PTAB documents are OCR'd with consistent formatting.
- Institution decisions also follow a formulaic structure with explicit per-Fintiv-factor headings — relevant for **ground-truth label extraction in evaluation only**, since the institution decision is post-T₀ and excluded as a feature source per `prediction_scope.md` §4.
- The full case record follows a consistent ordering: petition → preliminary response → institution decision → scheduling order → briefs → oral argument → final written decision. Only the petition is read for features.

## Competitive Landscape

- **Unified Patents**: Pulls PTAB API data, nice UI, no LLM analytics or factor-level classification
- **Docket Navigator**: Litigation research tool, tracks PTAB data, no LLM capabilities
- **General legal AI tools** (e.g., co-counsel products): Not patent-specific, not IPR-specific
- **Patent prosecution AI tools** (e.g., YC-backed startups): Focus on patent drafting, not IPR/PTAB analysis; do not access the PTAB API
- None of these tools do PTAB decision monitoring with factor-level classification

## Feature Engineering Priorities

All features must be observable at T₀ (petition filing) — see `prediction_scope.md` §4. Priorities:

1. **Petition binary flags**: Fintiv addressed (Y/N), 325(d) addressed (Y/N), Sotera stipulation present (Y/N) — all from petition §IV
2. **Petition counts**: claims challenged, prior-art references, exhibits, expert declarations, grounds (102/103) — from §I.B grounds table + exhibit list
3. **Temporal features**: petition filing date, policy-regime indicator (decision date is post-T₀ and excluded)
4. **Patent metadata**: technology center, patent age, counsel identity, NPE flag (assignment chain)
5. **Document-level features**: §IV section length, §I.B grounds-table density, §42.24 word-count utilization

The judge's per-factor Fintiv ratings and the dispositive factor are **excluded** as features (post-T₀ via institution decision); they remain available only as ground-truth labels for evaluation — see `ptab_scope_and_terminology.md` §5.4.

## Data Notes

- Only a subset of institution decisions address Fintiv — many do not and should be filtered (relevant only for label-evaluation use, not as a feature input).
- Practitioner has an annotated spreadsheet with labeled Fintiv sub-factors — potential **evaluation data** under the current scope, not training input.
- For discretionary denial classification, the institution decision alone is sufficient — but it's post-T₀ and excluded from features.
- Sotera stipulation: extractable from petition §IV.4 directly. Earlier framing assumed cross-referencing with district-court data (Docket Navigator) was needed; this is no longer required for our scope.
- District-court features beyond what petition §IV restates (e.g., judge docket congestion) remain genuinely external and out of scope for v1.
