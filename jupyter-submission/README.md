# IPR Outcome Prediction — Submission Bundle

Self-contained notebook + dataset for predicting IPR (Inter Partes Review) trial
outcomes at the U.S. Patent Trial and Appeal Board.

## Contents

- `01_getting_started.ipynb` — end-to-end notebook: preprocessor, time-aware split,
  walk-forward CV, evaluation, and a small grid search.
- `features.csv` — one row per IPR trial (n = 14,822). Already includes the
  engineered features **and** the columns the notebook needs for labeling
  and time-aware splitting (`cancelled`, `petition_filing_date`,
  `patent_number`, `decision_issue_date`, `termination_date`). Everything
  upstream of modeling (raw API pull → parse → join → feature build) has
  already been run; this CSV is the output of that pipeline.

## Running

```bash
pip install -r requirements.txt
jupyter notebook 01_getting_started.ipynb
```

Then run cells top-to-bottom. The notebook expects `features.csv` to sit next
to it (the default `CSV_PATH` is `Path("features.csv")`).

## Notes

- No dependency on the project package: the notebook is self-contained.
- The held-out tail (`holdout_after="2023-01-01"`) is never seen by CV or
  model selection — it is the deployment-honest evaluation slice.
- Maturity filter (`mature_days=600`) drops trials whose `cancelled` label
  hasn't crystallized yet — the resolving event (Final Written Decision or
  earlier termination) typically lands within ~20 months of petition filing,
  so a 600-day window ensures the outcome is final before training uses it.
