"""Modeling-side preprocessor: fit-on-train transforms applied per CV fold."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer, make_column_selector
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml_uspto.features.schemas.constants import (
    FREQUENCY_CATEGORICAL_COLUMNS,
    OHE_CATEGORICAL_COLUMNS,
)
from ml_uspto.models.schemas.constants import (
    MISSING_CATEGORY_SENTINEL,
    PRIOR_DATE_COLUMN,
    PRIOR_GROUP_COLUMNS,
    PRIOR_RESOLUTION_DATE_COLUMN,
)


class FrequencyEncoder(BaseEstimator, TransformerMixin):
    """Per-column rolling frequency encoding, gated by T₀.

    Used for open-vocabulary party identifiers
    (`petitioner_real_party`, `owner_real_party`). For a row at T₀ and
    column `c` with value `v`, returns the count of *training* rows
    where column `c` equals `v` and whose `date_column` is strictly
    before T₀. Unseen values, NaN, and values whose every training
    occurrence is ≥ T₀ all map to 0.

    The date gate keeps the count consistent with project T₀
    discipline: a row dated 2017 cannot see a 2020 filing the same
    party will eventually make. Refit per CV fold via sklearn
    `cross_validate` semantics.
    """

    def __init__(self, date_column: str = "petition_filing_date") -> None:
        """Stash the T₀-column name; no state until `fit`.

        sklearn's `clone()` requires constructor args be retrievable as
        same-named attributes.
        """
        self.date_column = date_column

    def fit(self, X: pd.DataFrame, y: object | None = None) -> "FrequencyEncoder":
        """Index per-(column, value) sorted train dates for rolling lookup."""
        X = self._as_frame(X)
        # Pull t0 date out of the frame
        dates = pd.to_datetime(X[self.date_column], errors="coerce").to_numpy()
        valid = ~pd.isna(dates)
        dates = dates[valid]
        order = np.argsort(dates, kind="stable")
        dates_sorted = dates[order]

        feature_cols = [c for c in X.columns if c != self.date_column]
        self.feature_names_in_ = np.asarray(feature_cols, dtype=object)

        self.group_dates_: dict[str, dict[object, np.ndarray]] = {}
        for col in feature_cols:
            # Apply the same valid-mask + date-sort permutation used on
            # `dates`, so vals_sorted[i] is the column value of the row at
            # dates_sorted[i] — walking it = walking train rows in
            # chronological order.
            vals_sorted = X[col].to_numpy()[valid][order]
            # Bucket positions by value. Python loop (not pandas groupby)
            # because keys can be arbitrary objects (NaN, occasional
            # tuples) that don't broadcast cleanly under numpy/pandas
            # vectorization. Because we iterate in date order, each list
            # comes out already date-sorted — exactly the precondition
            # `np.searchsorted` needs at transform time.
            idx_by_val: dict[object, list[int]] = {}
            for i, v in enumerate(vals_sorted):
                idx_by_val.setdefault(v, []).append(i)
            self.group_dates_[col] = {
                v: dates_sorted[np.asarray(idxs)] for v, idxs in idx_by_val.items()
            }
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        """Per row: count of pre-T₀ train occurrences of its (col, value)."""
        X = self._as_frame(X)
        q_dates = pd.to_datetime(X[self.date_column], errors="coerce").to_numpy()
        # NaT queries would otherwise sort as the max value under
        # `searchsorted` and silently return the full training count
        # — i.e. leak everything. Track the mask, force the search to
        # return 0 for those rows by zeroing the output at the end.
        nat_mask = pd.isna(q_dates)
        out = np.zeros((len(X), len(self.feature_names_in_)), dtype=np.int64)

        # For each column to compute frequency
        for j, col in enumerate(self.feature_names_in_):
            per_val = self.group_dates_.get(col, {})
            col_vals = X[col].to_numpy()
            rows_by_val: dict[object, list[int]] = {}
            for i, v in enumerate(col_vals):
                rows_by_val.setdefault(v, []).append(i)

            # For each category within the column, find counts prior to t0 by searching the timesorted array per_val.get(v) with fancy indexing.
            for v, row_idxs in rows_by_val.items():
                train_dates = per_val.get(v)
                if train_dates is None:
                    continue
                row_idxs_arr = np.asarray(row_idxs)
                idx = np.searchsorted(train_dates, q_dates[row_idxs_arr], side="left")
                out[row_idxs_arr, j] = idx
        if nat_mask.any():
            out[nat_mask, :] = 0
        return out

    def get_feature_names_out(
        self, input_features: list[str] | None = None
    ) -> np.ndarray:
        """Return `<col>_frequency` for each non-date input column."""
        if input_features is not None:
            names = [c for c in input_features if c != self.date_column]
        else:
            names = list(self.feature_names_in_)
        return np.asarray([f"{n}_frequency" for n in names], dtype=object)

    @staticmethod
    def _as_frame(X: object) -> pd.DataFrame:
        if isinstance(X, pd.DataFrame):
            return X
        return pd.DataFrame(np.asarray(X))


class PriorEncoder(BaseEstimator, TransformerMixin):
    """Per-row leakage-safe rolling cancellation rate over a group key.

    For a row at T₀ with group key `g`, returns:
      - `<label>_prior_rate`: mean of `cancelled` over training rows
        sharing group `g` whose **label-resolution date** (FWD-issue or
        termination) is strictly before T₀. Falls back to the global
        rolling rate (same gating) when the group has zero such history.
      - `<label>_prior_count`: how many qualifying training rows
        exist. Tells the downstream model how reliable the rate is —
        a count of 0 means the rate is the global fallback, not a
        learned group-specific signal.

    Why label-resolution date and not petition_filing_date.
    Per `docs/scope/prediction_scope.md` §4, base rates that aggregate
    over other trials' outcomes are admissible only over trials whose
    **terminating decision** issued strictly before T₀. A trial whose
    petition was filed before T₀ but whose FWD issued after T₀ has a
    label that wasn't observable at T₀ — including it leaks future
    information into the prior. So we order the cumulative sum by
    `resolution_date_column` (= `decision_issue_date` for FWD-resolved
    trials, else `termination_date`), not by petition_filing_date.

    Refit per CV fold: the rate at a fold's test rows is computed only
    from that fold's training portion (sklearn `cross_validate`
    semantics).

    Args:
        group_columns: One or more column names whose joined value is
            the group key. Length 1 → single-key prior (e.g. petitioner
            cancellation rate); length >1 → composite-key prior (e.g.
            petitioner-owner pair).
        date_column: Name of the T₀ column (default `petition_filing_date`).
            Used at transform time to query the rolling sum.
        resolution_date_column: Name of the label-resolution-date column
            on the *training* frame (default `label_resolution_date`).
            Used at fit time to order the cumulative sum so a training
            row contributes to a row's prior only after its label
            actually crystallized.
    """

    def __init__(
        self,
        group_columns: list[str],
        date_column: str = "petition_filing_date",
        resolution_date_column: str = "label_resolution_date",
    ) -> None:
        """Stash the column-name config; no state until `fit`.

        sklearn's `clone()` requires constructor args be stored verbatim,
        so we don't normalize here — `fit`/`transform` materialize a
        list when they need one.
        """
        self.group_columns = group_columns
        self.date_column = date_column
        self.resolution_date_column = resolution_date_column

    def fit(self, X: pd.DataFrame, y: pd.Series | np.ndarray | None = None) -> "PriorEncoder":
        """Index training (resolution_date, group, y) for cumulative-rate lookup.

        Sorts training rows by `resolution_date_column`, drops rows with
        unknown resolution date (rare; can't be gated), then per group
        stores the sorted resolution-date array and the cumulative `y`
        sum aligned with it. Plus a global rolling-sum index for the
        zero-count fallback.
        """
        if y is None:
            raise ValueError("PriorEncoder requires y at fit time")
        X = self._as_frame(X)
        y_arr = np.asarray(y, dtype=np.float64)
        res_dates = pd.to_datetime(
            X[self.resolution_date_column], errors="coerce"
        ).to_numpy()
        keys = self._build_keys(X)

        # A train row whose resolution date is unknown can't be gated
        # against any T₀, so it can't contribute to the rolling rate
        # without leaking. Drop these from the index entirely.
        valid = ~pd.isna(res_dates)
        res_dates = res_dates[valid]
        keys = keys[valid]
        y_arr = y_arr[valid]

        order = np.argsort(res_dates, kind="stable")
        dates_sorted = res_dates[order]
        keys_sorted = keys[order]
        y_sorted = y_arr[order]

        # Bucket by key via Python loop — tuple keys (composite groups)
        # break numpy `array == tuple` broadcasting, so we can't use a
        # vectorized mask here.
        idx_by_key: dict[object, list[int]] = {}
        for i, k in enumerate(keys_sorted):
            idx_by_key.setdefault(k, []).append(i)

        self.group_dates_: dict[object, np.ndarray] = {
            k: dates_sorted[np.asarray(idxs)] for k, idxs in idx_by_key.items()
        }
        self.group_cum_y_: dict[object, np.ndarray] = {
            k: np.cumsum(y_sorted[np.asarray(idxs)]) for k, idxs in idx_by_key.items()
        }

        self.global_dates_ = dates_sorted
        self.global_cum_y_ = np.cumsum(y_sorted)
        self.global_rate_ = float(y_arr.mean()) if len(y_arr) else 0.0
        self.feature_label_ = "_".join(list(self.group_columns))
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        """Vectorized lookup: per-group binary search, then global fallback.

        Output shape `(n_rows, 2)` — column 0 is the rate, column 1
        the count. The query date is the row's T₀; the per-group arrays
        store training rows' label-resolution dates (set in `fit`), so a
        training row contributes only when its label crystallized
        strictly before the query T₀. Rows whose group has zero such
        history get `count=0` and `rate` = global rolling rate at T₀.
        """
        X = self._as_frame(X)
        dates = pd.to_datetime(X[self.date_column], errors="coerce").to_numpy()
        keys = self._build_keys(X)

        # NaT queries would sort as max under `searchsorted` and silently
        # pull in the full training history. Mask them out and assign the
        # global unconditional fallback at the end (count stays 0).
        nat_mask = pd.isna(dates)

        out_rate = np.full(len(X), np.nan, dtype=np.float64)
        out_count = np.zeros(len(X), dtype=np.int64)

        # Group transform rows by their key in one Python pass — same
        # tuple-vs-numpy issue as in fit.
        rows_by_key: dict[object, list[int]] = {}
        for i, k in enumerate(keys):
            rows_by_key.setdefault(k, []).append(i)

        for group_key, row_idxs in rows_by_key.items():
            train_dates = self.group_dates_.get(group_key)
            if train_dates is None:
                continue
            cum_y = self.group_cum_y_[group_key]
            row_idxs_arr = np.asarray(row_idxs)
            idx = np.searchsorted(train_dates, dates[row_idxs_arr], side="left")
            sum_y = np.where(idx > 0, cum_y[np.clip(idx - 1, 0, len(cum_y) - 1)], 0.0)
            rate = np.where(idx > 0, sum_y / np.maximum(idx, 1), np.nan)
            out_rate[row_idxs_arr] = rate
            out_count[row_idxs_arr] = idx

        # Global rolling fallback for rows whose group had no pre-T₀
        # history (unseen group, or seen but every train occurrence is
        # ≥ this row's T₀). The model should still see the cohort base
        # rate at that point in time, not NaN.
        nan_mask = np.isnan(out_rate)
        if nan_mask.any():
            fb_idx = np.searchsorted(self.global_dates_, dates[nan_mask], side="left")
            sum_y_fb = np.where(
                fb_idx > 0,
                self.global_cum_y_[np.clip(fb_idx - 1, 0, len(self.global_cum_y_) - 1)],
                0.0,
            )
            rate_fb = np.where(
                fb_idx > 0,
                sum_y_fb / np.maximum(fb_idx, 1),
                self.global_rate_,
            )
            out_rate[nan_mask] = rate_fb

        # Override anything `searchsorted` produced for NaT queries: a
        # row with no T₀ can't be gated, so it gets the unconditional
        # fallback rate and count 0 — never the leak-prone full-history
        # tail searchsorted would have returned.
        if nat_mask.any():
            out_rate[nat_mask] = self.global_rate_
            out_count[nat_mask] = 0

        return np.column_stack([out_rate, out_count.astype(np.float64)])

    def get_feature_names_out(
        self, input_features: list[str] | None = None
    ) -> np.ndarray:
        """`<group_label>_prior_rate`, `<group_label>_prior_count`."""
        del input_features  # names are derived from `group_columns`, not the input slice
        return np.asarray(
            [
                f"{self.feature_label_}_prior_rate",
                f"{self.feature_label_}_prior_count",
            ],
            dtype=object,
        )

    def _build_keys(self, X: pd.DataFrame) -> np.ndarray:
        """Concatenate the group columns into a single hashable key per row.

        For composite keys, NaN components are coerced to a sentinel
        string before tupling. Reason: tuple equality compares elements
        via `==`, and `nan == nan` is False — two `(nan, "X")` tuples
        built from different NaN objects can fail to hash/compare equal,
        scattering rows that should bucket together. Sentinel-fill makes
        NaN groups behave like any other named group.
        """
        cols = list(self.group_columns)
        if len(cols) == 1:
            return X[cols[0]].astype(object).to_numpy()
        return X[cols].astype(object).fillna("__NA__").agg(tuple, axis=1).to_numpy()

    @staticmethod
    def _as_frame(X: object) -> pd.DataFrame:
        if isinstance(X, pd.DataFrame):
            return X
        return pd.DataFrame(np.asarray(X))


def attach_rolling_encodings(
    features: pd.DataFrame, y: pd.Series | np.ndarray
) -> pd.DataFrame:
    """Compute T₀-rolling categorical encodings once over the full corpus.

    `FrequencyEncoder` and `PriorEncoder` are both row-local: each row's
    output is a strict function of corpus rows whose date is strictly
    before that row's T₀. That gating is leakage-free regardless of
    which subset is used at fit, so refitting per CV fold (the previous
    setup) only throws away data — a fold's val row could legitimately
    use the corpus's full pre-T₀ history of every category, not just
    that fold's training prefix. We compute these once over the merged
    pre-modeling frame and attach them as plain numeric columns.

    Holdout→train leakage is structurally impossible here: training rows
    have T₀ < holdout cutoff ≤ every holdout row's `label_resolution_date`,
    so the strict-`<` gate inside the encoders never lets a holdout
    label feed a training row's encoding.

    Args:
        features: Full corpus frame, post label-merge. Must contain the
            group columns, `PRIOR_DATE_COLUMN`,
            `PRIOR_RESOLUTION_DATE_COLUMN`, and the
            `FREQUENCY_CATEGORICAL_COLUMNS`.
        y: Binary label aligned row-wise with `features`.

    Returns:
        A copy of `features` with `<group>_prior_rate`,
        `<group>_prior_count`, and `<col>_frequency` columns appended.
    """
    out = features.copy()
    y_arr = np.asarray(y)

    for group in PRIOR_GROUP_COLUMNS:
        enc = PriorEncoder(
            group_columns=list(group),
            date_column=PRIOR_DATE_COLUMN,
            resolution_date_column=PRIOR_RESOLUTION_DATE_COLUMN,
        ).fit(features, y=y_arr)
        arr = enc.transform(features)
        rate_name, count_name = enc.get_feature_names_out()
        out[rate_name] = arr[:, 0]
        out[count_name] = arr[:, 1]

    freq_cols = [*FREQUENCY_CATEGORICAL_COLUMNS, PRIOR_DATE_COLUMN]
    enc = FrequencyEncoder(date_column=PRIOR_DATE_COLUMN).fit(features[freq_cols])
    arr = enc.transform(features[freq_cols])
    for j, name in enumerate(enc.get_feature_names_out(freq_cols)):
        out[name] = arr[:, j]
    return out


def build_preprocessor() -> ColumnTransformer:
    """Build the per-fold preprocessor for the model-ready frame.

    Categorical rolling encodings (`PriorEncoder`, `FrequencyEncoder`)
    are computed once over the full corpus by `attach_rolling_encodings`
    upstream — they are leakage-free by row-local T₀ gating, and
    refitting per fold throws away pre-T₀ history without correctness
    benefit. This stage handles only per-fold-fittable transforms:

      - One-hot encoding for `OHE_CATEGORICAL_COLUMNS` (closed
        taxonomies — TC, CPC section). Missing values fill with a
        sentinel before encoding so NaN becomes an explicit "missing"
        level; column set frozen at fit time; `handle_unknown="ignore"`
        maps unseen test categories to all-zero rows.
      - `SimpleImputer(strategy="median")` over the remaining numeric
        columns (raw numeric features + the precomputed rolling
        encodings). Median is fit on train only.
    """
    ohe_branch = Pipeline(
        steps=[
            (
                "fill_missing",
                SimpleImputer(
                    strategy="constant",
                    fill_value=MISSING_CATEGORY_SENTINEL,
                ),
            ),
            (
                "encode",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype=int),
            ),
        ]
    )

    return ColumnTransformer(
        transformers=[
            (
                "ohe",
                ohe_branch,
                list(OHE_CATEGORICAL_COLUMNS),
            ),
            (
                "num_impute",
                SimpleImputer(strategy="median"),
                make_column_selector(dtype_include=np.number),
            ),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def build_pipeline(estimator: BaseEstimator) -> Pipeline:
    """Compose the preprocessor with `estimator` into a refit-per-fold pipeline."""
    return Pipeline([("preprocess", build_preprocessor()), ("model", estimator)])


__all__ = ["attach_rolling_encodings", "build_pipeline"]
