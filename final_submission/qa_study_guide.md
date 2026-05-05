# Study guide — IPR outcome modeling

A curated Q&A for teammates preparing to defend the modeling and feature-engineering portions of the project, plus the kind of probing questions the professor is likely to ask in the Q&A. Anchored to `final_submission/notebooks/01_getting_started.ipynb` and the production code under `src/ml_uspto/`.

**How to use.** Each question has a short answer (2–4 lines) and, where useful, a *deeper* answer with the exact code or doc that backs the claim. The bolded one-liners are the floor; the deeper notes are what to study if you want to handle follow-ups.

**Plain-English glossary** (terms that recur below; user-facing answers should always gloss these on first use):
- **IPR** — *Inter Partes Review*: a PTAB (Patent Trial and Appeal Board) proceeding where a third party challenges the validity of an already-granted patent.
- **Petitioner / Patent Owner** — the challenger / the patent holder.
- **T₀** — the day the petition is filed. Our prediction time.
- **FWD** — *Final Written Decision*: the PTAB's terminal ruling on a fully-instituted IPR. ~18 months after T₀ on average.
- **Cancelled** — the binary target: 1 if the FWD finds **all** challenged claims unpatentable, 0 otherwise. Includes adverse-judgment terminations.
- **Fintiv** — the discretionary-denial framework where PTAB declines to institute IPRs that overlap heavily with parallel district-court trials.
- **Sotera stipulation** — petitioner's binding promise not to re-raise the same invalidity grounds in district court; designed to defuse Fintiv.

---

## Section 1 — Problem framing (rubric: *clarity of problem formulation*)

### Q1. What exactly is the prediction task?

**At the moment an IPR petition is filed, what is the probability that the patent will be cancelled?** Concretely: given features observable at T₀ (petition filing date), predict whether the original FWD will hold *all* challenged claims unpatentable. One model, one decision, one label per petition. (`docs/scope/prediction_scope.md` §1.)

### Q2. Why predict outcome rather than institution?

Institution is a procedural threshold ("does the case go forward?"); cancellation is the substantive outcome the parties actually care about. A petitioner deciding whether to file, or a patent owner deciding how to respond, wants the end-of-trial probability — not the institution probability. We collapse both stages into one classifier rather than the cascade *P(institute) × P(cancel | instituted)* because the cascade requires two models, two labels, and a calibration step on a small dataset.

### Q3. Why is this useful in practice?

Three audiences: (1) **petitioners** allocating litigation budget — IPRs cost ~$500K and are the primary tool for clearing patent risk; a probability of cancellation lets you triage which patents to challenge; (2) **patent owners** scoping the threat from a freshly-filed petition; (3) **investors/analysts** pricing patent-heavy companies whose value depends on enforceability. The model isn't a litigation oracle — it's a *base-rate refiner* that beats "every petition has a ~50% chance of cancellation".

### Q4. Why is the target binary all-or-nothing rather than a 3-class (all/some/none) target?

Three reasons (`prediction_scope.md` §3.2):
1. **Petitioner-sweep framing.** The petitioner's "win" is invalidating *the patent*. Partial outcomes leave the patent owner with claims still to assert, so they're closer to a draw than a win in business terms.
2. **Threshold stability.** "Some claims unpatentable" is operationally fuzzy — which claims, what fraction, were they the asserted ones? The cover-page text doesn't say.
3. **Class balance.** Partial outcomes are ~5–10% of FWDs. A 3-class target creates a tiny middle class with high variance.

The cost is that the model implicitly answers the *petitioner*'s question. A patent-owner-side model would use the opposite cut (1 iff *no* claims unpatentable). Same regex catalog, different collapse.

---

## Section 2 — Data & label construction

### Q5. Where does the label come from?

Two sources (`prediction_scope.md` §3):
- **FWD trials** — the PDF cover page of the *original* FWD: phrases like `Determining All Challenged Claims Unpatentable` → 1, anything else → 0. The structured `decisionData.trialOutcomeCategory` is empty for IPRs (every FWD comes back as the bare string `Final Written Decision` — verified empirically: 0 of 2,180 FWDs carry a granular outcome), so we have to read the PDF.
- **Non-FWD terminations** — `trialStatusCategory` (`Institution Denied`, `Discretionary Denial`, `Terminated-Settled`, `Terminated-Adverse Judgment`, ...). All are 0 except `Terminated-Adverse Judgment` (patent owner concession → claims cancelled → 1).

### Q6. Why the maturity filter (`mature_days = 600`)?

A petition filed 6 months ago hasn't reached an FWD yet — its label is *unobservable*, not 0. Including such rows would teach the model that "recent petition" → "not cancelled", which is purely an artifact of the truncation. We drop trials whose petition is more recent than `today - 600 days` (~20 months, comfortably past the empirical median time from petition to FWD of ~18 months). This is a *right-censoring* fix.

### Q7. Why exclude pending and amendment FWDs?

- **Pending trials** (`Pending`, `Pending Director Review`, `Trial Instituted`) have no resolution yet — labelling them is guessing.
- **Amendment FWDs** (on-remand-CAFC, on-remand-Director, rehearing) rule on a *subset* of the originally-challenged claims; their cover-page outcome doesn't translate to a trial-level binary. We use only the original FWD per trial. (`prediction_scope.md` §3.1.)

### Q8. Aren't you discarding signal by collapsing settled cases to 0?

Yes — a settlement means the petitioner extracted enough leverage that the patent owner gave up, which is correlated with patent weakness. We keep `Terminated-Settled = 0` because no claims were *formally* cancelled, but flag it as a sensitivity test: re-train with settled = 1 and see whether AUC and feature importance shift materially. (`prediction_scope.md` §7.)

---

## Section 3 — Feature engineering

### Q9. Walk me through the feature families.

Four families, all observable at T₀:

1. **Patent file-wrapper structured features** — bibliographic (filing date, entity size, inventor count), CPC classification (technology center, CPC section), prosecution events (office actions, RCEs, prosecution span), assignment chain (NPE detection signals), maintenance fees. Computed from `Frame.PATENTS` parquet by `features.patent_aggregator.aggregate_patents`.
2. **Petition-text Tier A regex features** (5 columns) — `n_grounds`, `n_grounds_102`, `n_grounds_103`, `has_sotera_stipulation`, `mentions_fintiv_factors`. Extracted by `features.petition_text.aggregate_petition_text_row` over pdfplumber-extracted petition text.
3. **Categorical encodings** — `technology_center`, `cpc_section`, `ptab_era`, `entity_size`, `inventor_geo` (closed-vocabulary, OHE'd); `petitioner_real_party`, `owner_real_party` (open-vocabulary, frequency-encoded).
4. **Time-correct rolling base rates (priors)** — `PriorEncoder` over six group keys: petitioner, owner, petitioner×owner pair, technology center, patent number (multi-petition campaigns), and PTAB era (Director-memo regime).

### Q10. Why frequency-encode parties instead of one-hot or target-encoding?

`petitioner_real_party` and `owner_real_party` are *open-vocabulary* — thousands of distinct values, long-tailed, with new entrants every quarter. One-hot would create thousands of sparse columns, most of them seen once. Frequency encoding (count of training rows per category) compresses to one column while preserving the only signal that's genuinely predictive at this resolution: *is this a repeat player or a one-off?*. Target encoding (mean target per category) is a stronger signal but is what `PriorEncoder` already does for the same columns — more carefully, with date-gated rolling windows. (`notebooks/01_getting_started.ipynb` §3, cell 6.)

### Q11. Why one-hot the closed-vocabulary categoricals instead of integer-encoding?

Tree-based models (RF) are *robust* to integer encoding when categories have no ordinal meaning — but the splits get noisier because every threshold cut has to discover which integer-bucketed groups go together. OHE makes the categorical structure explicit and lets the RF pick clean per-level splits. The one-hot footprint is bounded (TC has ~9 levels, CPC section has 8, era has 4, entity size has 3, inventor_geo has 2) so column blowup is not a concern.

### Q12. Why insert a `MISSING_CATEGORY_SENTINEL` before OHE?

Missingness is itself signal in this domain — entity size is unknown for older patents predating the disclosure rule; inventor_geo is unknown when no inventor country is parseable; era is unknown for trials before our regime taxonomy starts. Encoding NaN as a sentinel string and letting OHE add a `__missing__` column turns missingness into an explicit level the model can split on, instead of silently imputing a mode that may not be meaningful. (`notebook` §3, `build_preprocessor`.)

### Q13. Walk me through `PriorEncoder` — what does it actually compute?

For each query row at T₀, and each group key (e.g. `petitioner_real_party`), it returns two numbers:
- **prior_rate** — average `cancelled` over training rows that share the group AND whose label was *resolved* before this row's T₀.
- **prior_count** — how many such rows there were.

Implementation (`notebook` cell 7):
1. **Fit** — sort training rows by `label_resolution_date` (FWD-issue date, else termination date), bucket by group key, store cumulative-sum-of-y per key.
2. **Transform** — for each query row, `searchsorted` finds how many training rows in the same group have a resolution date *strictly before* this row's `petition_filing_date`. Rate = cumsum_y / count.
3. **Cold-start fallback** — if a group has no qualifying history, fall back to the *global* rolling rate at T₀, not 0. (Returning 0 would systematically bias new entrants toward the "not cancelled" class.)

### Q14. Why gate priors on `label_resolution_date`, not `petition_filing_date`?

This is the subtle leakage point. Imagine training row A (petition filed 2020-01-01) whose FWD issued 2022-06-01, and query row B (petition filed 2021-09-01). If we gated on petition date, A's outcome would contribute to B's prior — but A's label *wasn't observable* on 2021-09-01, the day B was filed. The model would be using future information. Gating on the FWD/termination date enforces "only labels that had crystallized at T₀ count". (`prediction_scope.md` §4; `notebook` cell 7 docstring.)

### Q15. Why these specific group keys for priors?

Each captures a distinct domain hypothesis (`notebook` cell 4):
- `petitioner_real_party` — repeat-petitioner experience (Apple files hundreds of IPRs and is materially better at it than a one-off filer).
- `owner_real_party` — repeat-owner exposure (NPEs vs operating companies have different baselines).
- `(petitioner, owner)` — this exact adversarial pair's history (Apple vs VirnetX has its own trajectory).
- `technology_center` — TC base rate (some TCs are notoriously soft on validity).
- `patent_number` — multi-petition campaigns (a patent already partly cancelled is more likely to be fully cancelled in the next petition).
- `ptab_era` — PTAB policy regime (Iancu 2018+, Vidal 2022+, etc. — institution and cancellation rates shift with directorship).

### Q16. Why is the petition-text feature catalog so small (only 5 regex columns)?

Hard precision discipline (`CLAUDE.md` "Regex feature precision discipline"). A regex over PDF-extracted text is *only* defensible as a feature when both (a) every True-classified petition has the underlying legal context (no exhibit-name traps, no quoted opposing-party text, no token collisions) AND (b) the False-classified petitions don't routinely use *alternative phrasings* of the same concept. Three earlier candidates — `mentions_motivation_to_combine`, `mentions_general_plastic`, `mentions_aapa` — were dropped in May 2026 because the false-classification sweep found large recall gaps (alternative phrasings like "would have been motivated to combine" weren't matched). The five surviving features earned their place by passing both audits across a 124-trial integration cohort.

### Q17. What's the difference between `mentions_fintiv_factors` and "did the PTAB actually find Fintiv against the petitioner?"

The model never sees the second thing. `mentions_fintiv_factors` fires when the petitioner's §IV discussion lists ≥3 distinct Fintiv factors — *advocacy*, not *adjudication*. The actual factor-by-factor ruling lives in the institution decision, which is post-T₀ and excluded by the leakage rule. So the feature measures "did the petitioner think Fintiv was a real risk worth pre-empting", which itself is signal: a petition that hand-waves Fintiv is signaling weakness. This is a deliberate *construct-validity* tradeoff documented in `prediction_scope.md` §8.2 — flag it explicitly when discussing risks (Section 8 below).

---

## Section 4 — Preprocessor (the leakage-load-bearing piece)

### Q18. Why is the preprocessor a `ColumnTransformer` of branches inside a `Pipeline`?

The `Pipeline([("preprocess", ColumnTransformer), ("model", estimator)])` wrapper is what makes the preprocessor *refit per CV fold*. If you fit the preprocessor once on the full dataset and then run CV on the transformed matrix, frequency counts and prior rates would have been computed using rows from later folds — leakage. By keeping the preprocessor inside the pipeline, sklearn's `cross_validate` calls `pipeline.fit(X_train_fold, y_train_fold)` per fold, which refits *every* branch (frequency encoder, prior encoder, OHE, imputer) on training rows only. This is the single most important leakage discipline. (`notebook` §3 + §4.)

### Q19. What do the four `ColumnTransformer` branches do?

1. **Prior branches** (six of them, one per group key) — `PriorEncoder` returns (rate, count).
2. **Frequency branch** — `FrequencyEncoder` returns count per category.
3. **OHE branch** — `SimpleImputer(constant, "__missing__")` then `OneHotEncoder(handle_unknown="ignore")` so unseen test categories silently become all-zero rows rather than failing.
4. **Numeric imputer** — `make_column_selector(dtype_include=np.number)` then `SimpleImputer(strategy="median")` for any leftover numeric column.

`remainder="drop"` strips everything else — including `petition_filing_date` and `patent_number`, which only existed in `X` so the prior branch could read them.

### Q20. Why median imputation for numeric features rather than mean or model-based?

Numeric features here include heavy-tailed counts (n_office_actions, days_since_last_assignment, n_inventors) where mean is pulled by outliers. Median is robust and cheap. Model-based imputation (KNN, iterative) is overkill on a ~13.6K-row dataset (≈11.7K train + 1.9K held-out) and would itself need leakage discipline (refit per fold). The marginal AUC gain doesn't justify the complexity.

### Q21. Why does `OneHotEncoder` use `handle_unknown="ignore"` rather than `error`?

Open-world: a held-out test row may carry a category never seen at training time (rare technology center, brand-new entity-size code from a recent USPTO update). `ignore` returns an all-zero row for that column — graceful degradation. `error` would crash inference, which is unacceptable for a deployment-honest evaluation.

---

## Section 5 — Model choice & training procedure

### Q22. Why RandomForest?

Three reasons:
1. **Mechanical interpretability.** Domain experts (patent attorneys) can be walked through "this split says: if the petitioner is a repeat player AND tech center is 2400, the cancellation rate moves from base rate to a noticeably higher local rate". That story is harder with a gradient-boosted ensemble or a logistic model with strong regularization.
2. **No-tuning sanity.** RF works well out of the box on heterogeneous tabular data with mixed scales and missing values. We're not chasing 0.5% AUC — we're testing whether the leakage-disciplined pipeline produces a *defensible* signal.
3. **Robust to noisy categoricals.** Tree splits on frequency-encoded counts and prior rates compose well; gradient boosting is more sensitive to feature scale.

The user has explicitly asked us to stick to RandomForest only — no HGBT/XGBoost/stacking proposals.

### Q23. Walk me through the hyperparameters.

```python
RandomForestClassifier(
    n_estimators=500,
    max_depth=None,
    max_features=0.3,
    min_samples_leaf=5,
    class_weight="balanced",
    random_state=42,
    n_jobs=-1,
)
```

- **`n_estimators=500`** — enough trees that the OOB / CV variance settles; further trees add compute without moving AUC.
- **`max_depth=None`** — not capping depth was the single biggest lever in the sweep. Capping (`max_depth=10`) underfit badly relative to uncapped on the held-out tail. Justifiable because `min_samples_leaf=5` already prevents single-row leaves.
- **`max_features=0.3`** — each split sees ~25 of the ~85 post-preprocessing columns. `sqrt` (≈9) was too restrictive on this signal-poor matrix where many weak features need to compose. `1.0` overfits.
- **`min_samples_leaf=5`** — variance floor for rare counsel/party priors. Without it RF would memorize singleton groups via prior_rate.
- **`class_weight="balanced"`** — the post-maturity-filter cancellation rate is roughly 0.19 (≈19% of trials), so the majority class dominates and the unweighted classifier prefers it on threshold decisions. Balanced weighting upweights minority-class loss without resampling.
- **`random_state=42`** — reproducibility.
- **`n_jobs=-1`** — parallel tree fitting.

### Q24. How was the held-out winner picked?

A coarse sweep over `max_depth ∈ {None, 10, 15, 20}` × `max_features ∈ {sqrt, 0.3, 0.5}` × `min_samples_leaf ∈ {1, 5, 10}`, scored by 5-fold time-series CV ROC-AUC on the train slice (petitions before 2023-01-01). We ran the held-out tail evaluation **once**, on the CV-winning config — never iterated on the held-out number. (Doing otherwise turns the held-out tail into another validation set and inflates the reported AUC.)

### Q25. What does `class_weight="balanced"` actually do?

It sets per-class weights inversely proportional to class frequencies in the training fold: `w_i = n_total / (n_classes * n_class_i)`. Effectively, each minority-class row contributes more to the impurity calculation at every split, so trees are more willing to carve out regions where the minority class is dense. It's *not* the same as resampling — no rows are duplicated or dropped — but the optimization objective tilts toward minority recall.

---

## Section 6 — Validation strategy (rubric: *rigor of training and evaluation*)

### Q26. Why is a random K-fold split inappropriate here?

Because IPR outcomes drift with PTAB policy regimes (Iancu 2018+, Fintiv 2020, Vidal 2022, June 2025 framework reset). A random K-fold puts 2024 petitions in the training fold and 2020 petitions in the test fold — the model sees future *contemporary conditions* (Vidal-era cancellation rates) when scoring earlier petitions, and reported AUC reflects information that wouldn't be available at deployment. The result: optimistic, non-replicable numbers.

### Q27. Walk me through the time-aware split.

Two layers (`notebook` §4):
1. **Held-out tail** (`time_split`) — petitions with `petition_filing_date >= 2023-01-01` are quarantined as the deployment-honest evaluation slice. Never inspected during selection or hyperparameter search.
2. **Forward-walking CV** (`time_series_cv` + `_date_safe_folds`) — within the train slice, sklearn-`TimeSeriesSplit`-style 5 folds where each test fold sits *strictly after* its train rows by date.

Key extra: `_date_safe_folds` snaps each cut forward to the first row whose date is strictly greater than the previous row's. This prevents same-day filings (joinder cases, multi-petition campaigns filed in batches) from straddling a fold boundary, which would let one petition train and a sister petition test on the same day.

### Q28. Why a held-out tail rather than just CV?

Two distinct purposes:
- **CV** — model selection (which config has the best out-of-fold ROC-AUC).
- **Held-out tail** — *unbiased estimate of generalization to a future deployment window*. Reported once at the end, never used to choose anything.

If CV were both, every hyperparameter decision would silently consume validation data and the final number would be optimistic. This is the standard "test set" discipline, with the extra constraint that the test set is a temporal block, not a random sample.

### Q29. Why 5 CV folds?

Bias-variance tradeoff on the ~11.7K-row train slice. With 10 folds, the per-fold test set is ~1.2K rows, which starts to introduce ROC-AUC variance from small test slices. With 3 folds, the per-fold train set fits a stable RF but the spread of fold scores is too small to compare configs reliably. 5 is the standard middle.

### Q30. What if the held-out AUC is much worse than the CV AUC?

That's expected to some degree (held-out is later in time → more distributional shift), and it's the *honest* answer to "how would this model perform if deployed today". A large gap (say >0.10 AUC) would be a red flag of either (a) regime shift the model can't track or (b) leakage in CV that the held-out tail correctly excludes. In our case, CV ROC-AUC is ≈0.546 and held-out ROC-AUC is ≈0.576 — actually *higher* on the held-out tail, which is consistent with no leakage in CV (any leakage would push CV optimistically above held-out, not below) and with the held-out window happening to land on slightly more separable cohorts.

---

## Section 7 — Evaluation metrics (rubric: *reasoning and justification re: metrics*)

### Q31. Why ROC-AUC as the primary metric?

Three reasons:
1. **Threshold-free.** The model's output is a probability; the right threshold depends on the *user*'s cost ratio (a petitioner deciding whether to file vs a patent owner deciding whether to settle have different cost structures). ROC-AUC summarizes performance over all thresholds.
2. **Class-imbalance robust.** Accuracy is misleading at any imbalance; ROC-AUC is invariant to base rate.
3. **Probabilistic interpretation.** ROC-AUC = "probability the model ranks a random positive above a random negative". Direct and reportable.

### Q32. Why also track average precision and F1?

- **Average precision (PR-AUC)** — more sensitive than ROC-AUC at the high-precision end of the curve, which is the operating regime for petitioner-side decisions ("only file when we're confident the patent is weak"). ROC-AUC and PR-AUC can disagree under imbalance — reporting both is the honest move.
- **F1** — single-threshold sanity check at the default 0.5 cutoff. Useful for the confusion matrix discussion, less so as a model-selection signal.

### Q33. What does the held-out AUC of ~0.58 actually mean in domain terms?

ROC-AUC ≈ 0.576 means: given a random cancelled-petition and a random non-cancelled-petition, the model ranks the cancelled one higher ~58% of the time. That's modestly above chance (0.50) and well below "litigation oracle" (0.85+). In domain terms: useful only as a weak **base-rate refiner** — a petitioner currently working off the ~19% post-maturity base rate can lean on the model to nudge expectations up or down — but nowhere near a single-shot litigation prediction. This is the right framing for the report's critical-reflection section.

### Q33b. The reliability diagram in the report shows over-confidence in the 0.3–0.5 mid-range. Why didn't you fit a calibrator (Platt / isotonic) to fix it?

Three reasons we deliberately *report* miscalibration but do not *fit* a post-hoc calibrator:

1. **AUC-invariance.** Sigmoid (Platt) and isotonic calibration are both monotonic transformations. They re-shape the probability axis but cannot change the order in which cases are ranked, so they cannot move ROC-AUC. Whatever ranking power we have at 0.576 we'd still have post-calibration. The argument "model is weak, calibration would fix it" is a category error — calibration and ranking are orthogonal axes.

2. **Empirical carve-off cost on this cohort.** The chronologically honest way to fit a post-hoc calibrator is to hold out the *most-recent* slice of training data (so the calibrator generalizes forward in time, not backward) — typically via `CalibratedClassifierCV(cv='prefit')` or sklearn's modern `FrozenEstimator` wrapper. We measured the cost on this cohort: even a 5%-row chronological calibration slice loses ~3.6 pts of held-out AUC (0.576 → 0.540), and a 15% slice loses ~7.4 pts (0.576 → 0.502). That loss is entirely base-model degradation — sigmoid is monotonic, so by definition none of the AUC change comes from the calibrator. The rows we'd sacrifice are exactly the ones closest in distribution to the held-out tail. This is the same regime-shift effect we describe under §6 (distributional shift).

3. **Decision-support honesty.** The legal user wants `P(cancelled)` as input to an expected-value calculation. Reporting the reliability diagram alongside the raw probability tells them "trust the rank, distrust the absolute value of mid-range bins" — a transparent caveat. Fitting a calibrator hides the over-confidence under a fresh layer; if the underlying ranking is weak, a well-calibrated weak ranker is *more* dangerous to a non-ML user than a transparent miscalibrated one, because they read a 0.30 prediction as "30% chance" without knowing the model is at chance ranking.

The next-iteration fix isn't *post-hoc* calibration; it's a richer feature set that pushes ranking AUC up, at which point a calibrator would have something worth re-shaping. Cross-fitted calibration (`CalibratedClassifierCV(cv=time_aware_splitter)`) avoids the carve-off cost but produces an ensemble production object — out of scope for a pilot study.

### Q34. Walk me through the confusion matrix.

At the default 0.5 threshold:
- **True positives** — petitions correctly flagged as likely cancellations.
- **False positives** — petitions flagged as likely cancellations that actually survived → over-confident petitioner files a losing case.
- **False negatives** — petitions flagged as likely survivals that actually got cancelled → patent owner under-prepares for a real threat.
- **True negatives** — petitions correctly flagged as likely survivals.

The cost asymmetry is real: an FP costs the petitioner ~$500K in legal fees on a losing case; an FN costs the patent owner the patent itself. We don't currently optimize for either — `class_weight="balanced"` is a neutral starting point.

---

## Section 8 — Critical reflection (rubric: *depth of critical reflection*)

### Q35. What's the biggest *construct-validity* risk?

The petition-text features measure *advocacy* not *adjudication*. `mentions_fintiv_factors` says "the petitioner discussed Fintiv", not "the PTAB found Fintiv against them". The cleaner ground-truth signal lives in the institution decision, which is post-T₀ and excluded. So the model is partially predicting "did the petitioner write a thorough §IV?" rather than "is the patent weak?" — which is signal *correlated* with cancellation but isn't the underlying causal driver. This should be stated explicitly in the report.

### Q36. What's the biggest *distributional shift* risk?

PTAB policy regimes change roughly every 4 years with USPTO directorship. June 2025 introduced a new discretionary-denial framework that materially changes what gets instituted. A model trained on 2018–2024 data may degrade fast on 2025+ petitions — exactly the deployment window. Mitigations:
- The `ptab_era` feature lets the model condition on regime explicitly (but it can't generalize to a regime it hasn't seen).
- Time-aware CV is at least an honest *measurement* of degradation; quarterly re-training is the operational fix.
- Reporting both CV and held-out AUC documents the degradation rather than hiding it.

### Q37. Are there *fairness* concerns?

Two we've thought about:
- **Small entities.** `entity_size` (Regular/Small/Micro) is a feature; if the model systematically gives higher cancellation probabilities to small-entity patents, it could chill innovation in markets the patent system was designed to support (universities, startups). This requires a subgroup analysis we haven't done.
- **NPEs.** Owner-side priors will encode "NPE owner → higher cancellation rate" because NPEs litigate weaker patents on average. Whether *that's* a fairness issue depends on whether you think NPEs are a legitimate market participant — that's a policy question, not a modeling one. The model surfaces the pattern; we don't editorialize it.

### Q38. What other limitations should the report call out?

- **Single-jurisdiction scope.** IPR only — CBM (sunset 2020), PGR, INT, DER excluded. The model doesn't generalize to other PTAB proceeding types.
- **Petition-text-only PDF corpus.** We don't read POPR/POR/Reply (post-T₀) or expert declarations / parallel-suit exhibits (admissible but cut for cost). Tier 1/2 petition-text features (n_challenged_claims, n_prior_art_references, declaration paragraph counts, embeddings) are deferred to v2.
- **Right-censoring.** The 600-day maturity filter drops the most recent ~20 months of petitions, so the deployment window is always "where the world was ~20 months ago". Surrogate-outcome modeling (predicting institution as a stand-in for FWD) could close that gap but introduces its own confounding.
- **Settled trials labelled 0.** Defensible (claims aren't formally cancelled) but understates "petition pressure". Sensitivity test deferred.
- **Joinder petitions.** Currently included, identified only by `is_joinder` indicator. Stricter scope would exclude them; we kept them for sample size.

### Q39. Why isn't this just a "patent quality score" the way litigation-analytics vendors sell it?

Two reasons:
1. **It's conditional on a petition existing.** The training distribution is "patents that someone bothered to challenge in IPR". Patents that nobody has challenged — the vast majority — aren't in the model's experience. Applying this to a random patent extrapolates outside the training distribution.
2. **The label is *binary cancellation*, not *quality*.** Many strong patents survive IPRs; many weak patents are never challenged. The model captures one slice of the patent-quality manifold.

---

## Section 9 — Likely professor questions on code & decisions

### Q40. Show me where in the code leakage could enter and how you prevent it.

Three load-bearing pieces:

1. **`build_pipeline` wraps the preprocessor inside the sklearn `Pipeline`** (`notebook` §3, end of cell 8). This is what makes `cross_validate` refit every transformer per fold — without it, frequency counts and prior rates would be computed once on full data and leak into validation folds.

2. **`PriorEncoder` indexes by `label_resolution_date`, not `petition_filing_date`** (`notebook` §3, cell 7, fit method). A training row only contributes to a query row's prior once its FWD/termination date precedes the query's T₀. This is the difference between "your past in calendar time" and "your past in label-observability time".

3. **`time_split` + `_date_safe_folds`** (`notebook` §4, cell 10). Random K-fold would let the model train on 2024 outcomes when scoring 2020 petitions; date-snapped forward-walking CV prevents that, including the same-day-batch edge case for joinder filings.

### Q41. Why keep `petition_filing_date` and `patent_number` in `X` if they're not features?

`PriorEncoder` reads them at transform time — `petition_filing_date` is the T₀ for the query, `patent_number` is one of the group keys. The OHE / frequency / numeric branches' selectors don't pick them up, and `ColumnTransformer(remainder="drop")` strips them from the final design matrix the model sees. So they survive long enough to do their job and then disappear.

### Q42. Walk me through `_date_safe_folds` — what's the joinder-batch edge case?

PTAB allows multi-party joinder, where the same patent can be challenged by 5 different petitioners on the same day with substantively identical petitions. Standard `TimeSeriesSplit` cuts by row index, so a 5-petition same-day batch can have 3 rows train and 2 rows test, even though they're the *same petition by different filers* with strongly correlated outcomes. `_date_safe_folds` pushes each cut forward to the first row whose date is *strictly greater* than the previous row's, keeping same-day batches intact within one side of the split. Same chunk layout as TimeSeriesSplit, just snapped to date transitions. (`notebook` §4, cell 10.)

### Q43. Why fit the preprocessor on the full train slice after CV, before evaluating on the held-out tail?

CV is for *selection* (compare configs), not fitting. Once we've picked the config, we want the deployed model to use as much training data as possible — so we refit the entire pipeline (preprocessor + RF) on the full train slice (everything before 2023-01-01) and evaluate that fitted model once on the held-out tail. (`notebook` §8d, `run_training`.)

### Q44. What happens if you remove `class_weight="balanced"`?

The RF preferentially fits the majority class (non-cancelled, ~81%). ROC-AUC barely moves (it's threshold-free), but at the default 0.5 threshold, recall on the minority class drops further and the confusion matrix shifts — more false negatives. If a downstream user picks a non-default threshold (e.g. operating at high precision), the choice matters less. So the impact is mostly on the *interpretability of the default-threshold confusion matrix*, not the AUC story.

### Q45. What happens if you drop `PriorEncoder` entirely (only frequency + OHE + numerics)?

Empirically the priors carry a meaningful chunk of the model's signal — repeat-player priors and patent-number priors capture real information about who tends to win that the structural features don't. Run §10 of the notebook to quantify the gap on the current cohort; showing "with priors vs without priors" as a feature-ablation in the presentation validates that priors aren't decorative.

### Q46. Why didn't you try logistic regression / gradient boosting / a neural net?

Logistic regression we *did* run (`notebook` §9b) — CV AUC is comparable but with less dynamic range across configs, harder to interpret on a feature matrix this heterogeneous. Gradient boosting / neural nets aren't on the table per the user's standing instruction (interpretability + RF-only scope). The right framing in Q&A: "we deliberately scoped to a single, mechanically-interpretable model family because the project goal is to characterize the leakage-clean signal, not to maximize a leaderboard number."

### Q47. What's the next thing you'd do if you had another month?

Three concrete extensions:
1. **Tier 1 petition-text features** — `n_challenged_claims`, `n_prior_art_references`, declaration-paragraph counts. Higher-recall structural counts that pass the precision audit.
2. **Subgroup error analysis** — per-tech-center, per-entity-size, per-era confusion matrices. Surfaces whether the model is uniformly useful or has hot spots.
3. **Sensitivity tests from `prediction_scope.md` §7** — re-train with settled = 1, with joinder excluded, with the pre/post-2025 regime split; report whether AUC and feature importance shift materially.

### Q48. The model is at ~0.58 AUC. Why ship it?

Three reasons (this is the report's conclusion):
1. **It beats the base-rate prior, modestly but measurably.** Without the model, every petition gets the same ~19% post-maturity base rate. The model differentiates among them with non-zero signal — small, but reproducible across CV and the held-out tail.
2. **It's leakage-disciplined.** Most published "patent litigation prediction" numbers are higher *because they leak* (post-T₀ institution decisions, future-resolved priors, random K-fold over a regime-shifted timeline). An honest 0.57 is more useful than a fictitious 0.85.
3. **It's a starting point, not a ceiling.** Tier 1/2 petition-text features, subgroup analysis, and sensitivity-test outputs are scoped extensions that plausibly add AUC headroom. The pipeline is built to absorb them without re-doing the leakage discipline.
