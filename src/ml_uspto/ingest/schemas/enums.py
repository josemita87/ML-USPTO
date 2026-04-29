"""Ingest-local enums.

`Stage` enumerates the directories `ingest.cache` writes under
`<data.raw_dir>/<stage>/`. Same key shape will map 1:1 to S3 when the
backend swaps (`s3://<bucket>/raw/<stage>/<key>.json`).
"""

from enum import StrEnum


class Stage(StrEnum):
    """Cache buckets — one per fetch surface."""

    PROCEEDINGS = "proceedings"
    DOCUMENTS_PETITION_SCAN = "documents_petition_scan"
    DECISIONS = "decisions"
    PATENTS = "patents"
