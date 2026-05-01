"""Storage clients + settings-driven factory."""

from __future__ import annotations

from ml_uspto.settings import get_settings

from .local import LocalStorage
from .s3 import S3Storage


def get_storage():
    """Resolve the storage backend from settings.

    Reads `settings.storage` (which sources `ML_USPTO_STORAGE` and
    `ML_USPTO_S3_BUCKET` from env via pydantic-settings — never read env
    directly here). The Fargate task definition injects `backend=s3` +
    bucket name; laptop runs default to local.
    """
    cfg = get_settings().storage
    backend = cfg.backend.lower()
    if backend == "s3":
        if not cfg.s3_bucket:
            raise RuntimeError("storage.backend=s3 requires ML_USPTO_S3_BUCKET")
        return S3Storage(bucket=cfg.s3_bucket)
    if backend == "local":
        return LocalStorage()
    raise ValueError(f"unknown storage.backend={backend!r} (expected 'local' or 's3')")


__all__ = ["LocalStorage", "S3Storage", "get_storage"]
