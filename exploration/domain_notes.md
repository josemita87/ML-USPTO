# Domain Notes — IPR Institution Prediction

Insights gathered from conversations with a practicing patent attorney (partner at a major IP firm, ~10 years experience in PTAB proceedings).

## Target Variable

**Institution grant vs. denial** — the gatekeeper decision in IPR proceedings. If institution is denied, the petitioner has no recourse through this venue. Discretionary denial is procedural, not merits-based — you must pass this hurdle before reaching patentability analysis.

## High-Signal Features

### Binary Flags (extractable from petition / preliminary response)

| Feature | Description |
|---------|-------------|
| Fintiv addressed | Whether the decision addresses the six Fintiv factors (not all do) |
| 325(d) addressed | Whether the same prior art was already considered during original prosecution |
| Sotera stipulation | Whether the petitioner filed a stipulation differentiating defenses between district court and PTAB. Harder to automate — may require cross-referencing district court data (e.g., Docket Navigator) |

### Fintiv Sub-Factors (6 factors, ordinal scale)

Judges rate each factor using consistent, predictable phrasing:
1. "heavily favors" institution
2. "favors" institution
3. "neutral"
4. "weighs against" institution
5. "heavily weighs against" institution

Decision documents have **explicit headings** per factor ("factor one", "factor two", etc.), making extraction straightforward via term search or LLM classification.

Named factors include: grant of stay, trial date proximity, parallel proceedings, expert reliance, prior adjudication, and others tracked in practitioner spreadsheets.

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

- Institution decisions addressing Fintiv follow a formulaic structure with explicit per-factor headings
- All PTAB documents are OCR'd with consistent formatting
- IPR petitions have a two-part structure: procedural arguments (short, ~2-3 pages) and substantive arguments (bulk of document)
- The full case record follows a consistent ordering: petition -> preliminary response -> institution decision -> scheduling order -> briefs -> oral argument -> final written decision

## Competitive Landscape

- **Unified Patents**: Pulls PTAB API data, nice UI, no LLM analytics or factor-level classification
- **Docket Navigator**: Litigation research tool, tracks PTAB data, no LLM capabilities
- **General legal AI tools** (e.g., co-counsel products): Not patent-specific, not IPR-specific
- **Patent prosecution AI tools** (e.g., YC-backed startups): Focus on patent drafting, not IPR/PTAB analysis; do not access the PTAB API
- None of these tools do PTAB decision monitoring with factor-level classification

## Feature Engineering Priorities

1. **Binary flags**: Fintiv (Y/N), 325(d) (Y/N), Sotera stipulation (Y/N)
2. **Ordinal factor ratings**: 5-point scale per Fintiv sub-factor
3. **Temporal features**: Filing date, decision date, policy regime indicator
4. **Metadata**: Technology center, patent age, counsel identity
5. **Document-level features**: Discretionary denial section length, prior art count
6. **Dispositive factor identification**: Which factor drove the outcome

## Data Notes

- Only a subset of institution decisions address Fintiv — many do not and should be filtered
- Practitioner has an annotated spreadsheet with labeled Fintiv sub-factors — potential training/validation data
- For discretionary denial classification, the institution decision alone is sufficient
- For broader analysis (substantive, prior art strength), the full record is needed
- Cross-referencing with district court data (Docket Navigator) needed for some features (Sotera stipulation, case status) — this data is NOT in the PTAB API
