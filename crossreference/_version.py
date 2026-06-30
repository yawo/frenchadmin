"""Version stamp for the cross-reference inference pipeline.

Bump :data:`PIPELINE_VERSION` whenever a change to the extractor, normalizer,
resolver cascade, fuzzy resolver, semantic resolver, alias detector,
confidence scorer, or pipeline orchestrator would alter the inference output
for an unchanged source document AND an unchanged catalog snapshot.

The value is persisted alongside ``source_hash`` and ``catalog_hash`` in
``cross_reference_source_state``; a mismatch forces the document to be
re-processed even when its content has not changed. Without this, a deploy
that rewrites e.g. the resolver would silently leave previously-resolved
documents on the old logic until their LEGI/JADE/BOFIP source data
changed.

Format is free-form (a date stamp + counter is conventional). Only equality
matters; there is no ordering semantics.
"""

PIPELINE_VERSION = "2026.06.30-2"
