"""Feature row contract — the boundary between feature engineering and model code.

Mirrors the columns produced by `ml_uspto.features.transforms.build_features`.
Both training and inference paths construct `FeatureRow` instances; downstream
code (training, predict) consumes them. This is the single shared codepath
that prevents training/serving skew.
"""

from pydantic import BaseModel, ConfigDict, Field


class FeatureRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trial_number: str

    filing_year: int | None = None
    filing_month: int | None = None
    filing_dayofweek: int | None = None
    days_grant_to_petition: int | None = None

    petitioner_frequency: int | None = None
    owner_frequency: int | None = None

    art_unit_group: int | None = None
    technology_center: str | None = None

    # Variable-width one-hot for technology_center; persisted as a dict so the
    # contract is stable across training runs even when new TCs appear.
    # A tree model recovers per-TC base rates implicitly from the dummies; an
    # explicit mean-target-encoded rate would be redundant at this cardinality.
    tech_center_onehot: dict[str, int] = Field(default_factory=dict)
