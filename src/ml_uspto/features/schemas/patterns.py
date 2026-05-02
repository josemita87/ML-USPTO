"""Compiled regex patterns for `ml_uspto.features`."""

import re

NORMALIZE_NON_ALNUM = re.compile(r"[^a-z0-9]+")


__all__ = ["NORMALIZE_NON_ALNUM"]
