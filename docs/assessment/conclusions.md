# Project Assessment — Conclusions

Standing back from the engineering and scoping docs, this file records the
substantive conclusions we've reached about the prediction task itself: what
the modelling exercise has revealed about the *problem*, not the
implementation. Cross-references back to `scope/prediction_scope.md` and
`engineering/preprocessing.md` are intentional — those docs say *what we
built* and *how it works*; this doc says *what we learned by building it*.

Conclusions are added one section at a time as the project surfaces them.

---

## 1. The prediction target is non-stationary by construction

**Claim.** IPR (inter partes review — the PTAB administrative trial that can
cancel an issued patent) cancellation rates are not drawn from a fixed
distribution. The data-generating process shifts whenever the USPTO Director
changes or issues a major policy memo, and our `ptab_era` feature is an
explicit acknowledgment of that.

This is the textbook setup for **concept drift**, with two finer-grained
variants both at play:

- **Covariate shift.** `P(X)` changes — the era distribution is the
  cleanest example (every deployment-time petition lands in the latest
  era), but petitioner mix, technology-center mix, and ground-count
  distributions all drift along with the regime.
- **Prior probability shift / true concept drift.** `P(Y | X)` itself
  changes. The same petition profile (same petitioner, same technology
  centre, same number of §102 grounds) has materially different
  cancellation odds under Iancu's Fintiv regime than under Vidal's
  post-memo regime. The mapping the model learned in 2020-2022 is not
  the mapping that applies in 2025-2026. (§102 = the patent-law statute
  governing novelty challenges; Fintiv = the 2020 PTAB decision letting
  the Board deny institution when parallel district-court litigation is
  advanced.)

### 1.1 Why this is structural, not a data-collection bug

The Director of the USPTO is a presidential appointee. The §314(a)
discretionary-denial regime — the single biggest lever on whether IPR
petitions get instituted at all — is set by Director memos and
precedential designations, not statute. So every administration
transition is, by design, a regime change in the label-generating
process. The `config/ptab_eras.yaml` taxonomy enumerates seven such
regimes since 2018; there will be more.

This is a different problem from the usual "your training data is
stale" complaint, where retraining on fresher data fixes things. Here,
the *latest* era is by definition the one with the fewest mature-labelled
training rows (FWDs take ~18 months to crystallize — see the
`mature_days` filter in `drivers/run_train.py`), and the *deployment*
era is by definition the latest one. The model is always
extrapolating into the most under-sampled region of feature space.

### 1.2 Specific failure modes this induces

1. **OOV (out-of-vocabulary) eras at inference.** When a new Director
   takes office, every incoming petition belongs to an era the model has
   never seen. With `OneHotEncoder(handle_unknown="ignore")` the era
   one-hot becomes all zeros; the per-era cancellation prior in
   `PriorEncoder` falls back to the rolling global mean — i.e. the
   *historical* base rate, which is exactly the wrong anchor when the
   regime has just changed. The model degrades silently rather than
   failing loudly.

2. **Latent drift in non-era features.** Even with `ptab_era`
   stripped from the model, the regime change still propagates: petitioner
   mix shifts (some petitioners file more aggressively under loose §314(a)),
   ground-count distributions shift, technology-centre mix shifts.
   Removing the era column doesn't remove the drift — it just hides it
   from the model.

3. **CV optimism.** Forward-walking time-series CV exposes the
   train→future shift *within the training window*, but it cannot expose
   shift into a regime that hasn't started yet. CV ROC-AUC is therefore
   an upper bound on real deployment performance whenever a new era is
   imminent.

### 1.3 What this means for deployment

The honest framing: this is not a model that can be trained once and
left running. The retraining cadence has to be tied to *regime events*
(Director appointments, major §314(a) or claim-construction memos,
precedential designations), not to calendar time. A model trained
through Vidal-era data and deployed through a Trump-2 transition will
make systematically miscalibrated predictions until enough post-transition
FWDs accumulate to retrain — which, given the ~18-month FWD horizon,
is a multi-year lag.

Two practical mitigations are worth considering, neither of them
implemented yet:

- **A `days_since_era_start` continuous feature.** A brand-new era's
  one-hot column is all zeros and its prior is the global mean, but a
  small `days_since_era_start` value at least signals *that* the era is
  young. The model can learn (from prior eras) that early-era predictions
  should regress harder toward the new global mean.
- **Era-conditional prediction intervals.** Reporting a point probability
  during a regime transition is misleadingly precise. Calibrated intervals
  that widen when the era is under-represented at training time would
  communicate the actual epistemic state.

### 1.4 Why we keep `ptab_era` anyway

Dropping the feature doesn't fix the drift; it just denies the model a
domain-knowledge prior that the regime matters. Empirically, `ptab_era`
is one of the strongest signals in the feature matrix — institution rates
moved by tens of percentage points across the Fintiv/post-memo boundary.
Forcing the model to rediscover that boundary from collateral signals
is strictly worse than telling it. The cost is the silent-degradation
behaviour described above, which we accept on the condition that the
retraining cadence is regime-aware.
