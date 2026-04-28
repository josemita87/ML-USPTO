"""Constants for `ml_uspto.ingest`.

`STAGE_RECORDS_KEY` is not in YAML: these are USPTO ODP API-defined
response field names, not project configuration a domain expert would
revise. They live here so `ingest.fetch._paginate_cached` can look up
the records-bag key from a `Stage` without callers passing it inline.

`PETITION_SCAN_CATEGORIES` IS domain config — which
`documentData.documentCategory` values to scan for the corpus-wide
petition discovery pass. Loaded from `config/petition_picker.yaml` and
resolved to `DocumentCategory` enum members so callers stay off
free-string Literals (CLAUDE.md hard rule #3).
"""

import yaml

from ml_uspto import paths
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.schemas.enums import DocumentCategory

STAGE_RECORDS_KEY: dict[Stage, str] = {
    Stage.PROCEEDINGS: "patentTrialProceedingDataBag",
    Stage.DOCUMENTS_PETITION_SCAN: "patentTrialDocumentDataBag",
}


def _load_scan_filter() -> list[DocumentCategory]:
    with open(paths.PETITION_PICKER_YAML) as f:
        cfg = yaml.safe_load(f)
    return [DocumentCategory(s) for s in cfg["scan_filter"]["document_categories"]]


PETITION_SCAN_CATEGORIES: list[DocumentCategory] = _load_scan_filter()
