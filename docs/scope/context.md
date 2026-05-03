# Domain context — why this prediction problem is meaningful

Background that frames *why* IPR outcomes are predictable from petition-time information. Adjacent to but distinct from the formal contract: this doc explains the legal and political dynamics that motivate the feature catalog, while `prediction_scope.md` enumerates the rules and `glossary.md` defines the terms.

> The feature catalog and the regex patterns this doc references are documented in `../engineering/features/`.

---

## 1. Political regime cycles drive institution rates

Institution rates — the share of filed petitions that the PTAB agrees to hear — are heavily driven by the political appointment cycle of USPTO directors. Each new administration appoints a new director who can rewrite discretionary-denial policy by memo, and the docket responds.

| Period | Policy event | Effect on docket |
|---|---|---|
| 2020 | *Apple v. Fintiv* introduces the six-factor discretionary-denial test | Discretionary denials spike; institution rates fall |
| Dec 2020 | Sotera stipulation practice emerges as the patent-bar workaround | Institution rates partially recover |
| 2022 | Director Vidal's memo neutralizes most Fintiv factors | Institution rates surge |
| Mar 2025 | New memo reintroduces discretionary denial emphasis with 5–6 added factors | Denials spike again |
| Jun 2025 | New filing types appear (`petitioner discretionary brief`, `patent owner discretionary brief`) | Same API surface, new metadata identifiers |

The cycle across regimes is consistent: policy change → rate dip → adaptation → rate recovery → eventual reversal. March 2025 closely echoes the March 2020 shift.

**Modeling implication.** Petition filing date is a strong macro predictor — not because the date *causes* anything but because it proxies which discretionary-denial regime was active when the petition was filed. The time-aware evaluation requirement (`prediction_scope.md` §8.5) exists precisely so that this macro signal doesn't leak across regime boundaries during cross-validation.

---

## 2. Document structure makes regex extraction tractable

IPR petitions have a regulated structure (37 CFR §42.104) that every counsel follows because the rules require it:

- **§I.B** — grounds table (one row per ground: which claims, which statute, which references)
- **§II** — claim construction
- **§III** — prior-art mapping (the bulk of the document)
- **§IV** — discretionary considerations (Fintiv, Sotera, 325(d), General Plastic)
- **§VI** — mandatory notices (real party in interest, related matters, lead/back-up counsel)
- Footer — 37 CFR §42.24 word-count certification

This structural consistency is what makes Tier A petition-text features *possible*. Five regex patterns reliably extract `n_grounds`, `n_grounds_102`, `n_grounds_103`, `has_sotera_stipulation`, and `mentions_fintiv_factors` across the corpus precisely because every petition uses the same skeletal form. Pattern catalog: `src/ml_uspto/parse/schemas/patterns.py`.

The same is true of decision documents — institution decisions and FWDs follow a formulaic structure with explicit per-Fintiv-factor headings — but those are post-T₀ and serve label resolution only, never features.

---

## 3. Sotera stipulation: the binding commitment in §IV

Most §IV content is sales pitch — the petitioner's anticipated framing of how Fintiv *should* go before anyone has pushed back. There is one exception that's load-bearing.

The **Sotera stipulation** ("we will not raise these same invalidity arguments in district court") is a **binding commitment** the PTAB can hold the petitioner to. That single phrase is higher-signal than the rest of §IV combined: a petitioner who stipulates is signaling both case strength (willing to give up the courthouse alternative) and institutional sophistication (knows the doctrine that defuses Fintiv factor 4). The presence of a Sotera stipulation has been one of the most significant predictors of institution since *Sotera Wireless* in late 2020.

The Tier A `has_sotera_stipulation` feature is the regex that detects this — see `../engineering/features/features_csv_dictionary.md` §7 for the pattern variants and the audit cohort.

---

## 4. Revenue incentive against blanket denial

Each IPR petition generates a five-figure filing fee that the USPTO retains regardless of outcome. This creates a structural incentive against denying too aggressively — the patent office will not shut down the venue entirely. Discretionary denial waxes and wanes, but the docket has consistently remained open.

This is one reason the regime cycle described in §1 is *cyclic*: every administration that tightens denial eventually loosens it again, partly because petitioners (and their fees) start to flow elsewhere. Modeling-wise it means the trial-outcome distribution doesn't get pushed to extreme imbalance even during restrictive periods.

---

## 5. Competitive landscape

What exists in adjacent space, and what doesn't:

- **Unified Patents** — pulls PTAB API data, nice UI, no factor-level classification or LLM analytics.
- **Docket Navigator** — litigation research tool that tracks PTAB data alongside district-court dockets; no LLM capabilities.
- **General legal AI tools** (e.g., co-counsel products) — not patent-specific, not IPR-specific.
- **Patent prosecution AI tools** (YC-backed startups) — focus on patent drafting, not IPR/PTAB analysis; do not access the PTAB API.

None of these tools do PTAB decision monitoring with factor-level classification at the petition stage. The space this project lives in — *ex-ante* outcome prediction from petition-time signals — is largely empty.

---

## 6. Cross-references

- `prediction_scope.md` — the formal contract (task, T₀, leakage rule, label).
- `glossary.md` — what the legal terms mean (statutes, Fintiv, Sotera, POPR, …).
- `lifecycle_case_study.md` — concrete IPR2022-01002 walkthrough showing the full timeline.
- `../engineering/features/features_csv_dictionary.md` §7 — the Tier A pattern catalog this doc gestures at.
