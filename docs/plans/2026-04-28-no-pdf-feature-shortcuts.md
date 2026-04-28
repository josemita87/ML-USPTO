# No-PDF Feature Shortcuts — Consideration Note

**Date**: 2026-04-28
**Status**: consideration — not a decision. Captures what's recoverable from API metadata vs. what genuinely needs the petition PDF, so the v1 scope can be set deliberately.
**Scope**: which fields of `schemas.models.PetitionTextFeatures` can be derived without stage 5 (PDF download) + stage 6 (text extraction).

Authoritative companion docs: `../api/api_feature_map.md`, `../api/proceedings.md`, `2026-04-27-ingestion-pipeline.md`.

---

## 1. Why this note exists

The PDF pipeline (stages 5 + 6 in the ingestion plan) costs ~10 h cold-run wall-clock, ~70 GB local storage, a `pdfplumber` dependency, and ~7-10 working days of extractor + fixture engineering — see `2026-04-27-ingestion-pipeline.md` §2 for the cost model and §4 for the per-feature regex inventory.

Before committing to that, it's worth asking: **of the 14 fields in `PetitionTextFeatures`, how many can be derived from data we already pull at stages 1-2 (proceedings + documents/search)?**

Answer: 4 cleanly, 2 partially, 8 stay PDF-only. Documented below so the v1 PDF/no-PDF decision is explicit, not implicit.

---

## 2. Feature-by-feature shortcut inventory

| `PetitionTextFeatures` field | Shortcut? | Source | Notes |
|---|---|---|---|
| `petition_page_count` | ❌ | PDF | Document metadata doesn't expose page count. |
| `petition_word_count` | ❌ | PDF | Certificate of Word Count (§42.24) is in the petition body. |
| `n_claims_challenged` | ❌ | PDF | Petition §I content. Patent endpoint gives total claims as a denominator only. |
| `n_grounds` | ❌ | PDF | Grounds are a petition-internal taxonomy. |
| `n_grounds_102`, `n_grounds_103` | ❌ | PDF | Same; statute breakdown lives in the grounds table. `decisionData.issueTypeBag` lists statutes *addressed at FWD* — wrong semantics + post-T₀ leakage. |
| `n_exhibits` | ✅ | documents/search | Each exhibit is its own row with `documentCategory: "EXHIBIT"`. Filter by `filingPartyCategory == petitioner-side` and `documentFilingDate ≤ T₀ + small_window`. |
| `n_expert_declarations` | ✅ | documents/search | Subset of exhibits whose `documentTitleText` matches `^Declaration of `. |
| `n_prior_art_refs` | ✅ (proxy) | documents/search | Title-regex over petitioner-side exhibits: `^U\.?S\.?\s+Patent No\.`, `^U\.?S\.?\s+(Pre-Grant\s+)?Pub`, foreign-patent prefixes. ~75-80% accurate; arguably better than PDF-text parsing because exhibit titles are more structured than the in-petition reference list. |
| `has_sotera_stipulation` | ⚠ partial | documents/search | Catches stipulations *filed as standalone papers* (titles like "Stipulation Regarding Sotera Wireless"). Misses the more common case where the stipulation is buried in petition §IV.4. Estimated coverage: 20-30% of true positives. Null does NOT mean absent. |
| `mentions_fintiv_factors` | ❌ | PDF | Free-text discussion in the petition body. |
| `discloses_prior_iprs_same_patent` | ✅ | trials.parquet self-join | **Strictly better than the PDF approach.** Group `trials.parquet` by `patent_number`, count rows with earlier `petition_filing_date`. Ground-truth-accurate, deterministic, returns the actual count not just a boolean. |
| `n_real_parties_in_interest` | ⚠ partial | proceedings/search | `regularPetitionerData.realPartyInInterestName` is a single string and truncates joinder to the lead petitioner (per `api_feature_map.md`). Non-joinder cases (~95%): API gives the right answer. Joinder cases (~5%): undercounts; petition §VI.A is authoritative. |
| `claim_construction_disputed_terms` | ❌ | PDF | Petition §III; high formatting variance. Hardest extractor regardless. |

---

## 3. The four genuinely irreplaceable PDF features

Removing the shortcuttable / partial-shortcut fields leaves a hard core of features that *only* exist in the petition body:

1. `petition_word_count` — utilization of the §42.24 14,000-word ceiling.
2. `n_claims_challenged` — scope of the petition.
3. `n_grounds` (+ `_102` / `_103` split) — argument complexity.
4. `mentions_fintiv_factors` + `has_sotera_stipulation` (the stipulation-in-body case) — Fintiv-bypass posture.

`claim_construction_disputed_terms` exists in this set on paper but is the lowest-confidence extractor (~60% regex-only, see `2026-04-27-ingestion-pipeline.md` §4). Practically defer to v2 with an LLM extractor or skip entirely.

So the *real* PDF-only shortlist is **5-6 features**, not 14. The Sotera signal — frequently cited as the headline pre-Fintiv-bypass predictor — is in this list. So is petition word count.

---

## 4. Operational implication for v1

### 4.1 The "stage 2 rollup" extension

Stage 2 (corpus-wide petition discovery) already iterates every documents/search row — currently it picks the petition row and discards the rest. Extending it to *also* roll up per-trial document counts during the same scan is essentially free.

Proposed sibling model in `schemas/models.py`:

```python
class TrialDocumentRollup(BaseModel):
    """Per-trial document-count rollup, computed from documents/search at stage 2.

    Sourced from the same scan that produces `Petition` — counts
    petitioner-side filings within `petition_filing_date + small_window`
    so we get the petition's exhibit complement, not the whole trial's.
    """
    trial_number: str
    n_exhibits: int
    n_expert_declarations: int
    n_prior_art_refs_proxy: int
    sotera_paper_present: bool  # True only if a standalone Sotera paper was filed
```

Output: `data/processed/trial_document_rollup.parquet`, joined into `trials_joined.parquet` alongside `petitions.parquet`.

### 4.2 The `features/` self-join

`discloses_prior_iprs_same_patent` (and a more useful `n_prior_iprs_same_patent` count) belongs in the feature-engineering layer, not ingest:

```python
# features/derived.py (illustrative — not implemented)
trials["n_prior_iprs_same_patent"] = (
    trials.groupby("patent_number")
          .apply(lambda g: g.apply(
              lambda r: ((g.petition_filing_date < r.petition_filing_date)).sum(),
              axis=1))
          .reset_index(level=0, drop=True)
)
```

No new ingestion plumbing.

### 4.3 Resulting v1 feature inventory (no PDF)

| Tier | Features available without PDF |
|---|---|
| Proceedings (stage 1) | ~25 — see `api_feature_map.md` §2; covers temporal/regime, parties, tech center, patent metadata |
| Patent endpoint (stage 3) | ~13 — see `patents.md`; covers prosecution density, ownership history, PTA, continuity |
| **Documents rollup (stage 2 extension)** | **3 — `n_exhibits`, `n_expert_declarations`, `n_prior_art_refs_proxy`** |
| **Self-join (features/ layer)** | **1 — `n_prior_iprs_same_patent`** |
| Petition row (stage 2) | ~0 features (used as plumbing — petition_pdf_uri pointer + quarantine flag) |

**Total v1-without-PDF: ~42 features.** Ships in ~3-4 h cold-run wall-clock, no PDF storage, no `pdfplumber`.

**v1-with-PDF adds: ~5-6 high-signal features (Sotera-in-body, word count, n_claims_challenged, n_grounds, n_grounds_by_statute, mentions_fintiv).** ~10 h cold-run + ~70 GB + ~7-10 days of extractor work.

---

## 5. Decision points for the team

This note isn't recommending a path; it's surfacing the trade. The questions that actually matter:

1. **Is the Sotera signal worth the PDF pipeline alone?** If yes, the rest of the PDF features come along almost free once the infrastructure exists. If the question is "marginal predictive lift of Sotera-stipulation-in-body vs. just Sotera-stipulation-as-paper", that's an empirical question answerable only after a baseline run.

2. **Does the no-PDF v1 ship as a deliberate baseline, or as a contingency?** If it's "the baseline we measure v2 against", the rollup work in §4.1 is worth doing carefully. If it's "what we ship if PDF stages slip", less ceremony is needed.

3. **Do we trust `n_prior_art_refs_proxy` enough to use as the actual feature, or only as a sanity-check on the PDF version?** Empirically untested — we'd want to spot-check ~30 trials before relying on it.

4. **The `claim_construction_disputed_terms` field is barely worth either path.** Regex 60% accurate, no shortcut, requires §III parsing that's the hardest in the corpus. Candidate for "don't ship in v1, defer to LLM extractor in v2".

---

## 6. What would change in the codebase if we shortcut

If the rollup-extension path is taken:

- **`schemas/models.py`** — add `TrialDocumentRollup`. Keep `PetitionTextFeatures` as-is (it's the v2 surface).
- **`ingest/fetch.py::fetch_petitions`** — return a richer object: `(raw_doc_records, rollup_rows)` instead of just raw records. Or have `parse.petition_assembler.assemble_petitions` produce both `Petition` rows and `TrialDocumentRollup` rows from the same scan.
- **`config/parsers/documents.yaml`** — already covers the rollup fields (`documentCategory`, `documentTitleText`, `filingPartyCategory`, `documentFilingDate`); no schema change needed.
- **`features/`** — add the self-join derivation for `n_prior_iprs_same_patent`.
- **Drivers** — no change; `run_ingest_petitions.py` keeps its current shape, the assembler's output gains an extra parquet.

Things that *don't* change vs. the current plan:
- `clients/uspto.py` — no `download_pdf` / `stream_pdf` needed in v1.
- `clients/local.py` — no bytes-cache extension needed.
- `ingest/schemas/enums.py::Stage` — no `PETITIONS_PDF` or `PETITION_TEXT` member.
- `parse/petition_text.py` — doesn't exist yet, doesn't need to.
- `pdfplumber` — not in `pyproject.toml`.
- `data/raw/petitions_pdf/` — never created.

The PDF stages remain *additive* — the schema seams (`PdfFetchManifestRow`, `PetitionTextDoc`, `PetitionTextFeatures`) are already in `schemas/models.py` and stay stubbed. v2 turns them on without a refactor.
