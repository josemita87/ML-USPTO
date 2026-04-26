"""Prediction audit row — what gets written to the predictions table.

Target is binary per `docs/scope/prediction_scope.md` §3:
    1 = terminating FWD held all challenged claims unpatentable
    0 = anything else (institution denied, discretionary denial, settled,
        terminated procedurally, FWD where any claim survived)
    pending trials are excluded, never predicted.

Every prediction is tagged with `model_version` and `features_version` so a
historical row can be replayed deterministically when the underlying code changes.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Prediction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trial_number: str
    model_version: str
    features_version: str
    predicted_at: datetime
    prob_cancelled: float
    predicted_cancelled: int
    feature_snapshot_id: str | None = None
