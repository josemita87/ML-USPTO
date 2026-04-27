"""Project-wide constants, sourced from `config/labels.yaml`.

Keeping the taxonomies in YAML lets a domain expert revise the §3 rules of
`docs/scope/prediction_scope.md` without touching Python. Consumers import
the frozensets/strings here so there is exactly one Python entry point.
"""

from functools import lru_cache

import yaml

from ml_uspto.settings import PROJECT_ROOT

_LABELS_PATH = PROJECT_ROOT / "config" / "labels.yaml"


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(_LABELS_PATH) as f:
        return yaml.safe_load(f)


_labels = _load()

NON_FWD_LABEL_1_STATUSES: frozenset[str] = frozenset(_labels["non_fwd_label_1_statuses"])
NON_FWD_LABEL_0_STATUSES: frozenset[str] = frozenset(_labels["non_fwd_label_0_statuses"])
PENDING_STATUSES: frozenset[str] = frozenset(_labels["pending_statuses"])
FWD_DECISION_TYPE_MARKER: str = _labels["fwd_decision_type_marker"]
ALL_CLAIMS_UNPATENTABLE_OUTCOMES: frozenset[str] = frozenset(
    _labels["all_claims_unpatentable_outcomes"]
)
