"""Parse-local constants and helpers.

The top-level `ml_uspto.schemas` is for project-wide contracts. Constants
that are tightly bound to a single subpackage (e.g. the petition-picker
regexes) live here so they don't pollute the global schema while still
respecting the invariant: no scattered constants in code.
"""
