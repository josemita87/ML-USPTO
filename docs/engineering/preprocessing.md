# Modeling-side preprocessor

`features.csv` is leakage-free but row-local — every cross-row transform (rolling priors, frequency counts, medians, OHE column sets) lives on the modeling side. There are **two stages**, each with a different leakage-prevention contract:

1. **`attach_rolling_encodings`** — runs **once over the full corpus**, before the train/holdout split. Produces `*_prior_rate` / `*_prior_count` over six group keys plus `*_frequency` over the two open-vocabulary party fields. Leakage safety comes from a strict-`<` T₀ gate inside the encoders, not from per-fold refit.
2. **`build_preprocessor`** — a `ColumnTransformer` with two parallel branches that **refit per CV fold** (and again on full train before the held-out evaluation). Handles only OHE for closed taxonomies + median imputation over numerics.

| Stage | Branch | Inputs | Output | Refit cadence |
|---|---|---|---|---|
| 1 | `PriorEncoder` (×6) | group columns + `petition_filing_date` + `label_resolution_date` | `<group>_prior_rate`, `<group>_prior_count` | Once over full corpus (§1) |
| 1 | `FrequencyEncoder` | `petitioner_real_party`, `owner_real_party`, `petition_filing_date` | `<col>_frequency` | Once over full corpus (§1) |
| 2 | `ohe` | `technology_center`, `cpc_section`, `ptab_era`, `entity_size`, `inventor_geo` | one-hot columns | Per CV fold |
| 2 | `num_impute` | all remaining numeric cols (raw features + the stage-1 outputs) | imputed numerics | Per CV fold |

Stage 1 + stage 2 concatenate; that's what the model trains on. The post-OHE design matrix has 82 columns.

---

## 1. The corpus-level rolling encoders

Both `PriorEncoder` and `FrequencyEncoder` are **row-local under a strict-`<` T₀ gate**: each row's output is a function of corpus rows whose date is strictly before that row's T₀, computed via `searchsorted` on a per-group date array. That gating is leakage-free regardless of which subset of the corpus was used at fit time, so refitting per CV fold throws away pre-T₀ history without correctness benefit. We compute these once over the merged pre-modeling frame in `attach_rolling_encodings` and attach the outputs as plain numeric columns.

Holdout→train leakage is structurally impossible: training rows have T₀ < holdout cutoff ≤ every holdout row's `label_resolution_date`, so the strict-`<` gate inside the encoders never lets a holdout label feed a training row's encoding.

### 1.1 PriorEncoder — six rolling cancellation rates

For a row at T₀ with group key `g`, the encoder emits two columns:

- **`<group>_prior_rate`** — mean of `cancelled` over training rows that share group `g` and whose **label-resolution date** (`decision_issue_date.combine_first(termination_date)`) strictly precedes T₀.
- **`<group>_prior_count`** — how many such rows existed. Tells the model how reliable the rate is.

Six group keys are configured in `models.schemas.constants.PRIOR_GROUP_COLUMNS`:

```
("petitioner_real_party",)                       # repeat-petitioner experience
("owner_real_party",)                            # repeat-owner experience
("petitioner_real_party", "owner_real_party")    # this exact pair's history
("technology_center",)                           # TC base rate
("patent_number",)                               # multi-petition campaigns on one patent
("ptab_era",)                                    # period base rate (Director-memo regime)
```

**Why label-resolution date, not petition_filing_date.** A co-filed trial whose petition was filed before T₀ but whose FWD issued after T₀ has a label that wasn't observable at T₀. Including it leaks future information into the prior. So we order the cumulative sum by `resolution_date_column` (= `decision_issue_date` for FWD-resolved trials, else `termination_date`), not by petition_filing_date. See `../scope/prediction_scope.md` §4.

Implementation: at `fit` time the encoder sorts corpus rows by `label_resolution_date`, drops rows with unknown resolution date (rare; can't be gated), and stores `(sorted_dates, cumsum(y))` per group + globally. At `transform` time a `searchsorted` on the per-group date array gives `idx = #{corpus rows in this group with resolution_date < T₀}`; the rate is `cum_y[idx-1] / idx`. O((n + n_corpus) log n_corpus).

### 1.2 Cold-start fallback: rolling cohort rate, not zero

When a row's group has zero pre-T₀ history (`count == 0`), the encoder falls back to the **global rolling cancellation rate at T₀** — the corpus-wide cancellation rate among all corpus trials whose label crystallized strictly before this row's T₀.

The natural alternative is to fall back to `0` ("this petitioner has 0 prior cancellations, so rate = 0"). We rejected it for three reasons:

1. **`0` is not the empirical mean over zero samples** — it's undefined (`0/0`). Treating undefined as 0 systematically underpredicts cancellation for every first-time filer. Falling back to the cohort rate is the textbook Bayesian "no-data → use the prior" answer.

2. **Temporal asymmetry.** The set of trials available at T₀ is path-dependent: an early-2013 trial sits at a moment when almost no petitioner has any prior history (the AIA only created IPRs in late 2012). A `0` fallback would assign every 2013 prediction a flat-zero prior signal and the model would learn that 2013 trials don't cancel — except they did, at the period's actual rate. The rolling fallback captures the period-specific base rate because both branches share "as of T₀" semantics:
   - group-specific path: "cancellation rate among **this group's** trials whose label crystallized before T₀";
   - fallback path: "cancellation rate among **all** trials whose label crystallized before T₀".

   The cohort widens; the time anchor doesn't move.

3. **`prior_count` lets the model weight the rate explicitly.** With the rolling fallback both `rate` and `count` carry signal at the cold start: rate is an honest base-rate estimate, count communicates that it's a base-rate estimate. With a `0` fallback the model would have to learn "ignore rate when count == 0" — discarding one of the two features.

A non-rolling corpus-wide mean would be wrong differently: it leaks future cancellation rates into early-period rows (regime cycles drift the rate, see `../scope/context.md` §1).

### 1.3 The honest limitation

The current implementation is a hard switch: pure group-specific rate when `count > 0`, pure cohort rate when `count == 0`. With small `count`, the empirical rate is noisy (one cancellation out of one trial isn't really a 100% cancellation rate). A more principled design is **Bayesian shrinkage**:

```
shrunk_rate = (sum_y + α · cohort_rate) / (count + α)
```

Smooth transition from "all cohort" at `count = 0` to "all group" at `count >> α`. The current code is the limit `α → 0+` for `count > 0`, `α = ∞` for `count = 0`. Adopting `α ≈ 5–10` would reduce small-sample noise; not done in v1.

### 1.4 FrequencyEncoder — pre-T₀ occurrence counts

For each open-vocabulary party field (`petitioner_real_party`, `owner_real_party`) and a row at T₀, the encoder emits one column: the count of corpus rows where that field equals this row's value AND `petition_filing_date < T₀`. Unseen values, NaN values, and values whose every corpus occurrence is ≥ T₀ all map to 0. The implementation mirrors PriorEncoder's per-value sorted-date / `searchsorted` structure.

Note this gates on `petition_filing_date`, not `label_resolution_date` — a counterparty's *filing volume* is observable at the moment they file, regardless of when the resulting trials resolve.

---

## 2. The per-fold preprocessor

Stage 2 is the only thing `time_series_cv` actually refits per fold:

- **`ohe`** — `OneHotEncoder(handle_unknown="ignore", sparse_output=False)` over `OHE_CATEGORICAL_COLUMNS` (`technology_center`, `cpc_section`, `ptab_era`, `entity_size`, `inventor_geo`). NaN→`__missing__` sentinel before encoding so missingness becomes an explicit one-hot level rather than collapsing into all-zeros. Column set frozen at fit time; unseen test categories map to all-zero rows.
- **`num_impute`** — `SimpleImputer(strategy="median")` over the remaining numeric columns (35 raw numerics + the 14 stage-1 outputs). Median fit on train fold only.

`remainder="drop"` strips the leftover string columns (`petition_filing_date`, `patent_number`, `label_resolution_date`) — they were consumed by `attach_rolling_encodings` upstream and aren't needed by either branch.

---

## 3. Cross-references

- `src/ml_uspto/models/preprocessing.py` — `PriorEncoder`, `FrequencyEncoder`, `attach_rolling_encodings`, `build_preprocessor`, `build_pipeline`.
- `src/ml_uspto/models/schemas/constants.py` — `PRIOR_GROUP_COLUMNS`, `PRIOR_DATE_COLUMN`, `PRIOR_RESOLUTION_DATE_COLUMN`, `MISSING_CATEGORY_SENTINEL`, `MODELS`.
- `features/features_csv_dictionary.md` — what's in the input frame and why the train/test split lives at this boundary.
- `../scope/prediction_scope.md` §4 — the T₀ leakage rule.
- `../scope/context.md` §1 — regime cycles that motivate the rolling-vs-static choice.
