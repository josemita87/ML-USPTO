# Hyperparameter grid search

Grid search lives at `ml_uspto.models.tune.grid_search_cv` and runs
forward-walking date-safe CV per combo (the same `time_series_cv` used
by `models.train`). It cannot use sklearn's `GridSearchCV` because that
would shuffle folds and break the chronological discipline.

## Workflow

The deployment-honest split is preserved: grid search runs on the
training slice only; the held-out tail is locked until the model is
chosen.

1. **Sweep**: `uv run python drivers/run_grid_search.py` (defaults to
   all models in `ModelName`). Outputs land at
   `data/raw/metrics/grid_search_<model>.json` (every combo's per-fold
   AUCs + winner) and `_folds.png` (winner bold, all combos faint
   grey, x = chronological fold index).
2. **Pick**: read `best_params` from the JSON. Sanity-check the fold
   plot — a winner that's only winning because of one lucky fold is a
   signal to expand the grid, not to lock that combo.
3. **Lock**: paste the winning params into the corresponding
   `MODELS[ModelName.<X>]` factory in
   `src/ml_uspto/models/schemas/constants.py`.
4. **Eval**: `uv run python drivers/run_train.py --model <X>`. This
   refits with the locked params and runs `evaluate_model` on the
   held-out tail. **That** number is what you report.

## Models and grids

Approved estimators live in `MODELS`; their grids in `MODEL_GRIDS`
(`models.schemas.constants`). `RANDOM_FOREST` is a plain
`RandomForestClassifier`; `LOGISTIC` is a 2-step Pipeline
`(StandardScaler, LogisticRegression)` — scaling is mandatory because
the post-preprocessor matrix mixes prior counts (0…thousands),
frequency encodings (0…thousands), and binary OHE columns. Trees
ignore scale, so RF skips it.

Because `LOGISTIC`'s estimator is itself a Pipeline, its grid keys use
the `lr__` prefix (e.g. `lr__C`, `lr__penalty`) — these are sklearn's
nested-pipeline parameter paths consumed by `set_params`.

## Reproducibility knobs

`settings.yaml::model` pins the time anchors so a fresh `uv run` on a
later date produces identical splits:

- `experiment_today`: frozen "today" used for the mature-days math.
- `mature_days`: minimum age for a petition's label to be considered
  crystallized; rows newer than `experiment_today − mature_days` are
  dropped from both train and held-out.
- `holdout_after`: cutoff date that splits train / held-out tail.
- `cv_folds`: forward-walking fold count.

CLI flags on both drivers override each of these for one-off runs.
