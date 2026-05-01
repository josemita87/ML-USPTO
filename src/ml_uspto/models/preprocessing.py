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


_MISSING_CATEGORY_SENTINEL = "__missing__"


def build_preprocessor() -> ColumnTransformer:
    """Build the train-fit-only preprocessor for the model-ready frame.

    Three transformers, all refit per CV fold:
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
                    fill_value=_MISSING_CATEGORY_SENTINEL,
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


__all__ = ["FrequencyEncoder", "build_preprocessor", "build_pipeline"]
