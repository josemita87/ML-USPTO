"""Constants for `ml_uspto.ingest`."""

import yaml

from ml_uspto import paths
from ml_uspto.ingest.schemas.enums import Stage
from ml_uspto.schemas.enums import DocumentCategory

STAGE_RECORDS_KEY: dict[Stage, str] = {
    Stage.PROCEEDINGS: "patentTrialProceedingDataBag",
    Stage.DOCUMENTS_PETITION_SCAN: "patentTrialDocumentDataBag",
    Stage.DECISIONS: "patentTrialDocumentDataBag",
}


def _load_scan_filter() -> list[DocumentCategory]:
    with open(paths.PETITION_PICKER_YAML) as f:
        cfg = yaml.safe_load(f)
    return [DocumentCategory(s) for s in cfg["scan_filter"]["document_categories"]]


PETITION_SCAN_CATEGORIES: list[DocumentCategory] = _load_scan_filter()
