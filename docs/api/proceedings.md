# Proceedings vs Documents — Two Roles

The `/trials/proceedings/search` and `/trials/documents/search` endpoints are **complementary, not redundant**. An empirical probe on 2026-04-26 across six IPRs (Pending → Trial Instituted → FWD → FWD-Appealed → Settled → CAFC-remanded) showed that the trial header materializes very differently in the two endpoints.

## What we observed

| Trial | proceedings says `trialStatusCategory` | documents (petition row) says `trialStatusCategory` | petition row's `decisionData` |
|---|---|---|---|
| IPR2026-00339 | Pending | Pending | `null` |
| IPR2026-00130 | Trial Instituted | **Pending** (stale) | `null` |
| IPR2025-00954 | Final Written Decision | **Trial Instituted** (stale) | `null` |
| IPR2025-00748 | FWD - Appealed | **Trial Instituted** (stale) | `null` |
| IPR2026-00196 | Terminated-Settled | **Pending** (stale) | `null` |
| IPR2022-01002 (FWD doc, paper 52) | Final Written Decision | Final Written Decision | populated |

Two empirically-supported facts emerge:

1. **`trialMetaData` on a document row is a snapshot at that document's last-modification time, not the live trial state.** A petition row, which never updates after filing, basically captures the trial as it existed at T₀.
2. **`decisionData` is document-specific, not denormalized to every row.** It is populated only on decision-type document rows (FWDs, institution decisions, vacated/remanded). On a petition row it is `null`.

## What this means for ingestion

| Need | Endpoint | Why |
|---|---|---|
| **Trial inventory + live label state** (status, terminationDate, latestDecisionDate, current categorization) | `POST /trials/proceedings/search` | Maintains live trial-level state. One row per trial. Canonical source for the label. |
| **T₀ feature snapshot** (frozen-at-petition-filing trial / patent / party blocks + petition PDF URI) | `POST /trials/documents/search` filtered to `documentCategory: "PETITION"` | The denormalized header on the petition row is frozen at T₀. **This is a leakage-discipline win** — post-T₀ fields like `terminationDate` simply aren't there to leak. |
| **Label assembly without proceedings** (alternative path) | `POST /trials/documents/search` filtered to `documentCategory: "FINAL"` | FWD-type document rows have populated `decisionData` (`trialOutcomeCategory`, `issueTypeBag`, `statuteAndRuleBag`, `decisionIssueDate`) and current `trialMetaData`. Per-corpus probe shows 1,822 FINAL rows across IPR. |

## Field-by-field paths

For the legacy `proceedings` schema in `src/data/client.py`:

| Legacy column (proceedings) | Path on proceedings row | Path on documents/search petition row (frozen at T₀) |
|---|---|---|
| `trial_number` | `trialNumber` | `trialNumber` |
| `trial_type` | `trialMetaData.trialTypeCode` | `trialMetaData.trialTypeCode` |
| `petition_filing_date` *(T₀)* | `trialMetaData.petitionFilingDate` | `trialMetaData.petitionFilingDate` |
| `accorded_filing_date` | `trialMetaData.accordedFilingDate` | `trialMetaData.accordedFilingDate` |
| `patent_number` | `patentOwnerData.patentNumber` | `patentOwnerData.patentNumber` |
| `application_number` | `patentOwnerData.applicationNumberText` | `patentOwnerData.applicationNumberText` |
| `grant_date` | `patentOwnerData.grantDate` | `patentOwnerData.grantDate` |
| `group_art_unit` | `patentOwnerData.groupArtUnitNumber` | `patentOwnerData.groupArtUnitNumber` |
| `technology_center` | `patentOwnerData.technologyCenterNumber` | `patentOwnerData.technologyCenterNumber` |
| `inventor_name` | `patentOwnerData.inventorName` | `patentOwnerData.inventorName` |
| `owner_real_party` | `patentOwnerData.realPartyInInterestName` | `patentOwnerData.realPartyInInterestName` |
| `owner_counsel` | `patentOwnerData.counselName` | `patentOwnerData.counselName` |
| `petitioner_real_party` | `regularPetitionerData.realPartyInInterestName` | `regularPetitionerData.realPartyInInterestName` |
| `petitioner_counsel` | `regularPetitionerData.counselName` | `regularPetitionerData.counselName` |
| **`trial_status` (label)** | **`trialMetaData.trialStatusCategory`** (live) | `trialMetaData.trialStatusCategory` *(stale — petition-time snapshot)* |
| `institution_decision_date` | `trialMetaData.institutionDecisionDate` (live) | usually missing on petition rows |
| `latest_decision_date` | `trialMetaData.latestDecisionDate` (live) | usually missing |
| `termination_date` | `trialMetaData.terminationDate` (live) | usually missing |

The key takeaway is the **live vs frozen** distinction. Static fields (`patentNumber`, `grantDate`, art unit, party blocks) are identical in both. Mutable trial-state fields (`trialStatusCategory`, `terminationDate`, `latestDecisionDate`, `institutionDecisionDate`) are **live on proceedings, frozen near T₀ on the petition document row**.

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

**Resolution: per-trial POST + title matcher.** Empirically validated on 72 sampled legacy trials across 2014–2022: **100% recall, 0 misses** with the matcher below. The strategy:

1. For each trial in the proceedings inventory, `POST /trials/documents/search` with `filters: [{name: "trialNumber", value: [<trial>]}]`, paginated.
2. Apply a petition picker to the full doc list. Take the lowest `documentNumber` among rows that pass:

```python
import re

PETITION_TITLE = re.compile(r"\bpetition\b", re.I)
BLACKLIST = re.compile(
    r"power of attorney|notice of appeal|petitioner['’]?s|"
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
            continue          # exhibits often reference other petitions in their titles
        if num >= 100:
            continue          # paper numbers in 1xxx-2xxx range are exhibits
        if PETITION_TITLE.search(title) and not BLACKLIST.search(title):
            cands.append((num, dd))
    cands.sort()
    return cands[0][1] if cands else None
```

3. Fetch the PDF via `documentData.fileDownloadURI` from the picked row.

**Ambiguity classes the picker handles** (typical false positives across the 72-trial sample):
- "Notice of Filing Date Accorded to Petition" → blacklisted
- "Corrected Petition for Inter Partes Review" → ambiguous (paper 1 still wins on `documentNumber`)
- "Petitioner Motion to Correct Petition" / "Patent Owner's Motion To Deny The Petition" → blacklisted
- "Ex. 2017 Notice of IPR Petition" / "EX1020-Redlined Version of Proposed Corrected Petition" → filtered by category=Exhibit and paper-number ≥ 100
- "Expert Declaration ... in Support of Petition" → filtered by paper-number ≥ 100

## Ingestion implications

Use both endpoints, with clear role separation:

1. **Trial inventory & label** — `proceedings/search` paginated. Canonical list of all 18K IPR trials with live `trialStatusCategory`, `terminationDate`.
2. **Features at T₀ + petition PDF URI** — for each trial, `POST /trials/documents/search` filtered by `trialNumber`, then run `pick_petition()`. Header on the petition row is frozen at T₀ (leakage-safe).
3. **PDF download** — `GET <documentData.fileDownloadURI>` from the picked row.

The corpus-wide PETITION-only query (~6K rows) is *not* a viable shortcut — it misses ~12K legacy trials. The per-trial query is the actually-correct path.

See `api_feature_map.md` §1 for the full endpoint surface and `../scope/prediction_scope.md` §5.4 for the cost model.
