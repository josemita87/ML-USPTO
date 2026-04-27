# Proceedings vs Documents — Two Roles

The `/trials/proceedings/search` and `/trials/documents/search` endpoints are **complementary, not redundant**. They both attach a `trialMetaData` block to every row, but the two endpoints are served by **independent indexer pipelines that refresh on different cadences**, so the same field on the same trial can carry different values depending on which endpoint produced it.

## What we observed

Probe on 2026-04-27 across nine IPRs spanning the full lifecycle (just-filed → settled → instituted → institution-denied → FWD → FWD-Appealed → multi-year-old finished). For each trial we read `trialMetaData` from (a) the proceedings row and (b) the petition document row from `documents/search`:

| Trial | true state (proc.) | petition row says | doc-side stamp date | gap |
|---|---|---|---|---|
| IPR2026-00342 | Pending | Pending | 2026-04-24 | 0 d (just filed) |
| IPR2026-00130 | Trial Instituted | **Pending** | 2025-11-26 | ~5 mo |
| IPR2026-00088 | Trial Instituted | **Pending** | 2025-12-23 | ~4 mo |
| IPR2026-00108 | Institution Denied | **Pending** | 2026-02-12 | ~2 mo |
| IPR2026-00196 | Terminated-Settled | **Pending** | 2026-02-05 | ~2 mo |
| IPR2025-00954 | Final Written Decision | **Trial Instituted** | 2025-09-22 | ~7 mo (refreshed at institution) |
| IPR2012-00004 | Terminated-Settled | Terminated-Settled | 2016-07-10 | matched (both quiet since 2016) |
| IPR2012-00001 | Final Written Decision | Final Written Decision | 2022-10-10 | matched (both quiet since 2022) |
| IPR2022-01002 | Final Written Decision | **FWD - Appealed** | 2026-02-09 | ~2 mo |

The actual mechanic — what `trialMetaData` on a document row really is:

1. **It's the trial-level header denormalized onto every document row at trial-index time** — *not* a per-document or per-filing-date snapshot. Within one trial, paper 1, paper 3 (petition), paper 9, paper N all carry the *same* `trialMetaData` block, time-stamped by the same `trialLastModifiedDateTime`.
2. **The proceedings indexer and the documents indexer refresh independently.** The proceedings side updates within days of each trial event (institution, FWD, mandate, termination). The documents side lags — minutes for a freshly-filed trial, multi-month for active ones, multi-year for trials that have gone quiet.
3. **There is no T₀ snapshot anywhere in the API.** The petition row does *not* preserve "the trial as it existed when the petition was filed" — it preserves the trial as it existed when the documents indexer last touched the trial, which can be arbitrarily after T₀.
4. **For old finished trials, the two endpoints converge** because both pipelines have gone quiet long enough to land on the same stamp (see IPR2012-* in the table). For active trials, they diverge (see IPR2022-01002 — proceedings says `FWD`, documents-side stamp from 2 months ago still says `FWD - Appealed`).
5. **`decisionData` is document-specific, not denormalized.** It is populated only on decision-type document rows (FWDs, institution decisions). On a petition row it is `null`. *(This single field is the only piece of `documents/search` that is genuinely per-row.)*

The naive read of "documents/search returns a petition snapshot" — used by an earlier draft of this doc and confirmed by a 6-trial sample of recent (2025–2026) trials — was wrong: that sample landed on trials where the documents-side lag *happened* to keep the stamp near T₀, but for older finished trials the lag has long since elapsed and the petition row carries fully post-T₀ status / dates.

## The proceedings status stops at "FWD reached" — the verdict is only on `/decisions`

Empirical (probe of 1,000 IPR2022 records, 2026-04-27): the deepest the proceedings `trialStatusCategory` ever gets for an FWD-completed trial is **`Final Written Decision`** or **`Final Written Decision - Appealed`**. It does *not* distinguish between the three FWD verdicts that drive the label:

- All challenged claims unpatentable → label 1
- Mixed (some claims survived) → label 0
- All claims patentable → label 0

The verdict-level field (`trialOutcomeCategory == "All Challenged Claims Unpatentable"` etc.) lives only on `decisionData` from `/trials/decisions/search`. Full-record dumps of FWD-status proceedings rows (IPR2025-00954 "Final Written Decision", IPR2025-00748 "Final Written Decision - Appealed", IPR2025-00742 "Terminated-Adverse Judgment") confirmed there is no hidden verdict field on the proceedings side.

**Empirical `trialStatusCategory` taxonomy (1000-record IPR2022 sample):**

| Status | Count | Label-construction role |
|---|---:|---|
| `Terminated-Settled` | 334 | Status alone → label 0 |
| `Final Written Decision` | 286 | Status alone insufficient — consult decisions endpoint |
| `Institution Denied` | 258 | Status alone → label 0 |
| `Final Written Decision - Appealed` | 92 | Status alone insufficient — consult decisions endpoint |
| `Terminated` | 15 | Status alone → label 0 (procedural) |
| `Terminated-Adverse Judgment` | 12 | Status alone → label 1 (patent-owner concession under 37 CFR § 42.73(b); claims are cancelled). ⚠ Subject to domain-expert review — see `../scope/prediction_scope.md` §3 / §7 sensitivity test. |
| `Pending Director Review` | 2 | Pending — exclude |
| `Terminated-Dismissed` | 1 | Procedural — label 0 |

(Plus `Pending` and rarely `Trial Instituted` from broader sweeps. All three pending vocabularies are now in `config/labels.yaml::pending_statuses`.)

**Why this means we still need `/trials/decisions/search`:** without it, every trial in the two FWD buckets above (~378 of 1000 in the 2022 sample, projecting to ~6,400 in the full IPR corpus) is unlabelable. Decisions = labels.

### Resolution applied to `config/labels.yaml`

- `pending_statuses` widened to `[Pending, Pending Director Review, Trial Instituted]` (was `[Trial Instituted]` only).
- `non_fwd_label_0_statuses` extended with `Terminated-Dismissed`.
- New top-level key `non_fwd_label_1_statuses: [Terminated-Adverse Judgment]`. `parse/preprocessing.py` now resolves the label by checking, in order: label-1-by-status, label-0-by-status, FWD verdict from the decisions side. Marked subject to domain-expert review in case the project's "what counts as cancelled?" definition diverges from the legal default.

## What this means for ingestion

| Need | Endpoint | Why |
|---|---|---|
| **Trial inventory + live label state** (status, terminationDate, latestDecisionDate, current categorization) | `POST /trials/proceedings/search` | Maintains the freshest trial-level state. One row per trial. Canonical source for the label. |
| **Petition PDF URI + T₀ confirmation** | `POST /trials/documents/search` filtered by `trialNumber`, then `pick_petition()` | The petition row carries `documentData.fileDownloadURI` and `documentData.documentFilingDate` (= T₀). **Use only `documentData.*` fields from this row.** `trialMetaData` on the row is lagged and may be post-T₀. |
| **Label assembly via decisions** (alternative to proceedings status) | `POST /trials/documents/search` filtered to `documentCategory: "FINAL"`, or `POST /trials/decisions/search` | FWD-type document rows have populated `decisionData` (`trialOutcomeCategory`, `issueTypeBag`, `statuteAndRuleBag`, `decisionIssueDate`). Per-corpus probe shows 1,822 FINAL rows across IPR. |

## Field-by-field paths

For the proceedings schema used by `ml_uspto.parse.preprocessing`:

| Column | Path on proceedings row | Path on documents/search petition row | Leakage class |
|---|---|---|---|
| `trial_number` | `trialNumber` | `trialNumber` | static |
| `trial_type` | `trialMetaData.trialTypeCode` | `trialMetaData.trialTypeCode` | static |
| `petition_filing_date` *(T₀)* | `trialMetaData.petitionFilingDate` | `trialMetaData.petitionFilingDate` | T₀ itself |
| `accorded_filing_date` | `trialMetaData.accordedFilingDate` | `trialMetaData.accordedFilingDate` | T₀-adjacent (≤ T₀ + ~30d) |
| `patent_number` | `patentOwnerData.patentNumber` | `patentOwnerData.patentNumber` | static |
| `application_number` | `patentOwnerData.applicationNumberText` | `patentOwnerData.applicationNumberText` | static |
| `grant_date` | `patentOwnerData.grantDate` | `patentOwnerData.grantDate` | static, < T₀ |
| `group_art_unit` | `patentOwnerData.groupArtUnitNumber` | `patentOwnerData.groupArtUnitNumber` | static |
| `technology_center` | `patentOwnerData.technologyCenterNumber` | `patentOwnerData.technologyCenterNumber` | static |
| `inventor_name` | `patentOwnerData.inventorName` | `patentOwnerData.inventorName` | static |
| `owner_real_party` | `patentOwnerData.realPartyInInterestName` | `patentOwnerData.realPartyInInterestName` | usually static; can change at assignment |
| `owner_counsel` | `patentOwnerData.counselName` | `patentOwnerData.counselName` | usually static |
| `petitioner_real_party` | `regularPetitionerData.realPartyInInterestName` | `regularPetitionerData.realPartyInInterestName` | static |
| `petitioner_counsel` | `regularPetitionerData.counselName` | `regularPetitionerData.counselName` | static |
| **`trial_status` (label)** | **`trialMetaData.trialStatusCategory`** | ⚠️ lagged — do not use as feature | label |
| `institution_decision_date` | `trialMetaData.institutionDecisionDate` | ⚠️ lagged | label |
| `latest_decision_date` | `trialMetaData.latestDecisionDate` | ⚠️ lagged | label |
| `termination_date` | `trialMetaData.terminationDate` | ⚠️ lagged | label |

**Source-of-truth rule.** `trialMetaData` and the static blocks (`patentOwnerData`, `regularPetitionerData`) are *available* in both endpoints, but treat the **proceedings row as canonical**. Use the documents endpoint **only** for `documentData.*` fields on the petition row (`fileDownloadURI`, `documentFilingDate`, `documentNumber`, `documentTitleText`). Pulling features from `trialMetaData` on a document row risks silent post-T₀ contamination — see the lag table above.

## Probe-derived caveats

- **Filter case-insensitivity.** `documentCategory: "PETITION"` and `documentCategory: "Petition"` return the same 5,999 rows. The facet returns the upper-case form.
- **`documentTypeName` is unindexed.** Filtering or faceting on it returns empty — use `documentCategory` instead.
- **GET vs POST.** `/trials/documents/search` is **POST-only** — `GET` returns 404. Same for `/trials/decisions/search`.
- **Per-trial GET endpoint is capped at 25 rows with no pagination.** Use the cross-trial POST endpoint (`/trials/documents/search` filtered by `trialNumber`) instead — it paginates to the full document list.

## Petition coverage and the category-taxonomy drift

A naive filter on `documentCategory: "PETITION"` returns only **5,999 rows** vs **18,058 IPR trials** — a 67% coverage gap. Probing showed this is **not missing data**; it's a category-taxonomy drift across years.

**Year-by-year coverage of `documentCategory: "PETITION"` against proceedings:**

```
Year     proceedings     PETITION rows     coverage
2014           1501                49           3%
2017           1720                89           5%
2020           1442               224          16%
2021           1301               570          44%
2022           1322               926          70%
2023           1154              1173         102%
2024           1316              1320         100%
2025           1206              1213         101%
```

The post-2022 taxonomy uses fine-grained categories (`PETITION`, `MOTION`, `RESPONSE`, `REPLY`, `FINAL`, …). Pre-2022 the taxonomy collapses **all procedural papers into the `Paper` bucket** (294,925 rows corpus-wide) — petitions, motions, orders, mandatory notices share the same label. There is no separate "petition_legacy" category to add to the filter.

A multi-value filter `documentCategory IN ["PETITION", "Paper"]` returns **300,924 rows**, of which only ~6% are actual petitions. Category alone cannot disambiguate.

**Resolution: corpus-wide scan + title matcher + paper-number ceiling.** The canonical implementation lives in `src/ml_uspto/parse/petition_picker.py`. Empirical validation on a stratified probe of 239 trials (2012–2025, all terminal statuses): **98.7% clean recall, 0 false positives.** The remaining 1.3% fall through to a quarantine list rather than feeding wrong PDFs into the feature pipeline — the right failure mode for a leakage-sensitive system.

The strategy:

1. **One corpus-wide scan**, not per-trial. `POST /trials/documents/search` with `filters: [{name: "documentCategory", value: ["PETITION", "Paper"]}]`, paginated. Returns ~301K rows in ~3K calls of 100/page — far cheaper than the 18K per-trial calls in the earlier draft of this doc.
2. **Apply the picker per trial** (group by `trialNumber`). Take the lowest `documentNumber` among rows that pass:

```python
import re

PETITION_TITLE = re.compile(
    r"\bpetition\b|"
    r"\binter\s+part(?:e|ie)s\s+review\s+of\b|"
    r"\brequest\s+for\s+(?:inter\s+part(?:e|ie)s\s+review|ipr)\b",
    re.I,
)

# `petitioner['’]?s\s+(?!petition\b)` uses a negative lookahead so "Petitioner's
# Reply" / "Petitioner's Mandatory Notices" are rejected while
# "Petitioner's Petition for Inter Partes Review" passes through.
BLACKLIST = re.compile(
    r"power of attorney|notice of appeal|"
    r"petitioner['’]?s\s+(?!petition\b)|"
    r"notice of (filing date accorded|accord)|"
    r"request for (refund|rehearing)|sur-?reply|surreply|"
    r"response to petition|denying institution|institution of inter partes|"
    r"motion for joinder|grant of motion for joinder",
    re.I,
)

def pick_petition(rows):
    cands = []
    for row in rows:
        dd = row.get("documentData") or {}
        title = dd.get("documentTitleText") or dd.get("documentName") or ""
        cat = (dd.get("documentCategory") or "").lower()
        num = dd.get("documentNumber") or 9999
        if cat in ("exhibit", "exhibits"):
            continue
        if num >= 10:                 # 94% of real petitions are paper 1–3, none seen past paper 8
            continue
        if PETITION_TITLE.search(title) and not BLACKLIST.search(title):
            cands.append((num, row))
    cands.sort(key=lambda x: x[0])
    return cands[0][1] if cands else None
```

3. **Reconcile + quarantine.** Left-join the picked rows against the proceedings inventory by `trialNumber`. Any trial without a picked row goes to a quarantine list for manual inspection — at 18K trials × ~1.3% miss = ~230 cases, manageable. **Do not silently drop quarantined trials.**
4. **PDF download** — `GET <documentData.fileDownloadURI>` from each picked row.

### Ambiguity classes the picker handles

Three failure modes were observed in earlier picker versions and are now defended against. The empirical evidence behind each is the 239-trial probe (2026-04-27).

| Failure mode | Real example (trial → title) | Defense |
|---|---|---|
| Title omits "petition" | IPR2012-00005 → "Inter Partes Review of 6,653,215" | `\binter\s+part(?:e\|ie)s\s+review\s+of\b` alternative |
| Title uses "Request for IPR" | IPR2013-00064 → "Request for IPR of U.S. Patent No. 7,923,311" | `\brequest\s+for\s+...\bipr\b` alternative |
| Typo: "Petitioner for…" instead of "Petition for…" | IPR2024-01238 → "Petitioner for Inter Partes Review of U.S. Patent No. 8,830,821" | "inter partes review of" alternative catches it |
| Blacklist too aggressive on real petitions | IPR2021-00285 → "Petitioner's Petition for Inter Partes Review of US Pat No. 10,468,047" | `petitioner['’]?s\s+(?!petition\b)` lookahead lets it through |
| False positive: "Notice of Filing Date Accorded to Petition" | IPR2013-00072 picked this (paper 5) when no real petition matched | `notice of (filing date accorded\|accord)` added to blacklist |
| False positive: corrected petition displaces original | IPR2020-01483 → V1 picked paper 8 "Corrected Petition", missing the real petition at paper 2 | `documentNumber < 10` ceiling + paper-number sort |
| Exhibits with "petition" in title | "Ex. 2017 Notice of IPR Petition", "EX1020-Redlined Version of Proposed Corrected Petition" | `documentCategory == exhibit` filter |
| Expert Declaration ... in Support of Petition | High paper number on declarations | `documentNumber < 10` ceiling |

The full set of failure-mode fixtures lives in `tests/unit/test_petition_picker.py` — every example above is locked in as a regression test.

## Ingestion implications

Use both endpoints, with clear role separation:

1. **Trial inventory, features, and label** — `proceedings/search` paginated. Canonical source for all `trialMetaData` and party-block fields. One row per trial with the freshest `trialStatusCategory`, `terminationDate`, etc.
2. **Petition PDF URI + T₀ confirmation** — for each trial, `POST /trials/documents/search` filtered by `trialNumber`, then run `pick_petition()`. Read **only** `documentData.*` from the picked row. Ignore `trialMetaData` on this row — it is lagged and can carry post-T₀ values (see "What we observed").
3. **PDF download** — `GET <documentData.fileDownloadURI>` from the picked row.

The corpus-wide PETITION-only query (~6K rows) is *not* a viable shortcut — it misses ~12K legacy trials. The per-trial query is the actually-correct path.

See `api_feature_map.md` §1 for the full endpoint surface and `../scope/prediction_scope.md` §5.4 for the cost model.
