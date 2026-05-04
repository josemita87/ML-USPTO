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
    """Per-column frequency encoding fit on training rows only.

    Used for open-vocabulary party identifiers
    (`petitioner_real_party`, `owner_real_party`). Counts are corpus
    aggregates, so they must be refit per CV fold to avoid leakage.
    Categories absent from training (including NaN if it was not
    observed in train) map to 0.
    """

    def fit(self, X: pd.DataFrame, y: object | None = None) -> "FrequencyEncoder":
        """Learn `value_counts(dropna=False)` per column from `X`."""
        X = self._as_frame(X)
        self.feature_names_in_ = np.asarray(X.columns, dtype=object)
        self.counts_: dict[str, dict[object, int]] = {
            col: X[col].value_counts(dropna=False).to_dict() for col in X.columns
        }
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        """Map each value to its train-time count; unseen categories → 0."""
        X = self._as_frame(X)
        out = np.zeros((len(X), len(self.feature_names_in_)), dtype=np.int64)
        for j, col in enumerate(self.feature_names_in_):
            counts = self.counts_.get(col, {})
            out[:, j] = X[col].map(counts).fillna(0).astype(np.int64).to_numpy()
        return out

    def get_feature_names_out(
        self, input_features: list[str] | None = None
    ) -> np.ndarray:
        """Return `<col>_frequency` for each input column."""
        names = (
            list(input_features) if input_features is not None else list(self.feature_names_in_)
        )
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

        return np.column_stack([out_rate, out_count.astype(np.float64)])

    def get_feature_names_out(
        self, input_features: list[str] | None = None
    ) -> np.ndarray:
        """`<group_label>_prior_rate`, `<group_label>_prior_count`."""
        return np.asarray(
            [
                f"{self.feature_label_}_prior_rate",
                f"{self.feature_label_}_prior_count",
            ],
            dtype=object,
        )

    def _build_keys(self, X: pd.DataFrame) -> np.ndarray:
        """Concatenate the group columns into a single hashable key per row."""
        cols = list(self.group_columns)
        if len(cols) == 1:
            return X[cols[0]].astype(object).to_numpy()
        return X[cols].astype(object).agg(tuple, axis=1).to_numpy()

    @staticmethod
    def _as_frame(X: object) -> pd.DataFrame:
        if isinstance(X, pd.DataFrame):
            return X
        return pd.DataFrame(np.asarray(X))


def build_preprocessor() -> ColumnTransformer:
    """Build the train-fit-only preprocessor for the model-ready frame.

    Four transformer kinds, all refit per CV fold:
      - `PriorEncoder` per entry in `PRIOR_GROUP_COLUMNS` — leakage-safe
        rolling cancellation rate + count for a group key (petitioner,
        owner, pair, technology center, patent). Each requires
        `petition_filing_date` to be present on the input frame.
      - `FrequencyEncoder` for `FREQUENCY_CATEGORICAL_COLUMNS`
        (open-vocabulary party identifiers). Counts learned on train,
        unseen test categories map to 0.
      - One-hot encoding for `OHE_CATEGORICAL_COLUMNS` (closed
        taxonomies — TC, CPC section). Missing values are filled with
        a constant sentinel *before* encoding so NaN becomes an
        explicit "missing" level. The column set is frozen at fit time;
        `handle_unknown="ignore"` maps unseen test categories to
        all-zero rows.
      - `SimpleImputer(strategy="median")` for any remaining numeric
        columns. Median is a corpus aggregate, fit on train.

    Returns:
        A `ColumnTransformer` ready to drop into a `Pipeline`.
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

    prior_branches = [
        (
            f"prior_{'_'.join(group)}",
            PriorEncoder(
                group_columns=list(group),
                date_column=PRIOR_DATE_COLUMN,
                resolution_date_column=PRIOR_RESOLUTION_DATE_COLUMN,
            ),
            [*group, PRIOR_DATE_COLUMN, PRIOR_RESOLUTION_DATE_COLUMN],
        )
        for group in PRIOR_GROUP_COLUMNS
    ]

    return ColumnTransformer(
        transformers=[
            *prior_branches,
            (
                "freq",
                FrequencyEncoder(),
                list(FREQUENCY_CATEGORICAL_COLUMNS),
            ),
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


__all__ = ["build_pipeline"]
