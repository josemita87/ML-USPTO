# Modeling-side preprocessor

`features.csv` is leakage-free but row-local — every cross-row transform (frequency counts, medians, target-style priors) is deferred to `models.preprocessing.build_preprocessor`, which is **refit per CV fold** so that encodings learned on the train fold never see the test fold.

The preprocessor is a `ColumnTransformer` with four parallel branches.

| Branch | Inputs | Output | What it produces |
|---|---|---|---|
| `prior_<group>` (×5) | group columns + `petition_filing_date` | `<group>_prior_rate`, `<group>_prior_count` | Leakage-safe rolling cancellation rate over a group key (see §1). |
| `freq` | `petitioner_real_party`, `owner_real_party` | `<col>_frequency` | Train-fold value counts. Unseen test categories → 0. |
| `ohe` | `technology_center`, `cpc_section` | one-hot columns | NaN→`__missing__` sentinel before encoding (so missingness is a level, not all-zeros). Column set frozen at fit time; `handle_unknown="ignore"`. |
| `num_impute` | all remaining numeric cols | imputed numerics | `SimpleImputer(strategy="median")`. Train-fold median only. |

The four branches concatenate; that's what the model trains on.

---

## 1. The prior encoder

For a row at T₀ with group key `g`, the encoder emits two columns:

- **`<group>_prior_rate`** — mean of `cancelled` over training rows that share group `g` and were filed strictly **before T₀**.
- **`<group>_prior_count`** — how many such rows existed. Tells the model how reliable the rate is.

Five group keys are configured in `models.schemas.constants.PRIOR_GROUP_COLUMNS`:

```
("petitioner_real_party",)                       # repeat-petitioner experience
("owner_real_party",)                            # repeat-owner experience
("petitioner_real_party", "owner_real_party")    # this exact pair's history
("technology_center",)                           # TC base rate
("patent_number",)                               # multi-petition campaigns on one patent
```

Implementation: at `fit` time the encoder sorts training rows by date and stores `(sorted_dates, cumsum(y))` per group + globally. At `transform` time a `searchsorted` on the per-group date array gives `idx = #{train rows in this group with date < T₀}`; the rate is `cum_y[idx-1] / idx`. O((n_test + n_train) log n_train).

### 1.1 Leakage discipline

Two layers:

- **Strict `<` on the date.** Scoring trial X uses outcomes of *earlier* trials only. The trial's own outcome cannot bleed into its own features.
- **Refit per CV fold.** The sklearn `Pipeline` ensures `fit` only sees the fold's training rows. A test-fold row never contributes to its own prior, even via the global rate.

This is conservative versus deployment — at deployment every prior trial's outcome is genuinely available — so it can only underestimate the deployed model's quality.

### 1.2 Cold-start fallback: rolling cohort rate, not zero

When a row's group has zero pre-T₀ history (`count == 0`), the encoder falls back to the **global rolling cancellation rate at T₀** — the corpus-wide cancellation rate among all training trials filed strictly before this row's T₀.

The natural alternative is to fall back to `0` ("this petitioner has 0 prior cancellations, so rate = 0"). We rejected it for three reasons:

1. **`0` is not the empirical mean over zero samples** — it's undefined (`0/0`). Treating undefined as 0 systematically underpredicts cancellation for every first-time filer. Falling back to the cohort rate is the textbook Bayesian "no-data → use the prior" answer.

2. **Temporal asymmetry.** The set of trials available at T₀ is path-dependent: an early-2013 trial sits at a moment when almost no petitioner has any prior history (the AIA only created IPRs in late 2012). A `0` fallback would assign every 2013 prediction a flat-zero prior signal and the model would learn that 2013 trials don't cancel — except they did, at the period's actual rate. The rolling fallback captures the period-specific base rate because both branches share "as of T₀" semantics:
   - group-specific path: "cancellation rate among **this group's** trials filed before T₀";
   - fallback path: "cancellation rate among **all** trials filed before T₀".

   The cohort widens; the time anchor doesn't move.

3. **`prior_count` lets the model weight the rate explicitly.** With the rolling fallback both `rate` and `count` carry signal at the cold start: rate is an honest base-rate estimate, count communicates that it's a base-rate estimate. With a `0` fallback the model would have to learn "ignore rate when count == 0" — discarding one of the two features.

A non-rolling corpus-wide mean would be wrong differently: it leaks future cancellation rates into early-period rows (regime cycles drift the rate, see `../scope/context.md` §1).

### 1.3 The honest limitation

The current implementation is a hard switch: pure group-specific rate when `count > 0`, pure cohort rate when `count == 0`. With small `count`, the empirical rate is noisy (one cancellation out of one trial isn't really a 100% cancellation rate). A more principled design is **Bayesian shrinkage**:

```
shrunk_rate = (sum_y + α · cohort_rate) / (count + α)
```

Smooth transition from "all cohort" at `count = 0` to "all group" at `count >> α`. The current code is the limit `α → 0+` for `count > 0`, `α = ∞` for `count = 0`. Adopting `α ≈ 5–10` would reduce small-sample noise; not done in v1.

---

## 2. Cross-references

- `src/ml_uspto/models/preprocessing.py` — `PriorEncoder`, `FrequencyEncoder`, `build_preprocessor`, `build_pipeline`.
- `src/ml_uspto/models/schemas/constants.py` — `PRIOR_GROUP_COLUMNS`, `PRIOR_DATE_COLUMN`, `MISSING_CATEGORY_SENTINEL`, `MODELS`.
- `features/features_csv_dictionary.md` — what's in the input frame and why the train/test split lives at this boundary.
- `../scope/prediction_scope.md` §4 — the T₀ leakage rule.
- `../scope/context.md` §1 — regime cycles that motivate the rolling-vs-static choice.
