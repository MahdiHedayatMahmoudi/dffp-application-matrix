# Pipeline v3.5.3 changes

Pipeline v3.5.3 is a deterministic hardening revision of the FAIRagro DFFP
provenance-aware extraction pipeline.

It retains:

- DFFP schema: `fairagro-dffp-v3.5.2`
- system prompt: `system_v2`
- extraction prompt: `extraction_v12`
- repair prompt: `repair_v4`

No schema migration is required solely because the pipeline implementation
identifier advanced from v3.5.2 to v3.5.3.

## Main changes

- Repair consolidation preserves source-supported initial evidence.
- Canonical quantitative metrics are deduplicated deterministically.
- Duplicate evidence records are removed without losing distinct support.
- Structured-table semantics are enriched deterministically.
- Cross-target evaluation-population contamination is prevented and cleaned.
- Provenance paths are normalized for portability and privacy.
- Existing outputs can be deterministically revalidated without another LLM call.

## Release validation

The v3.5.3 release candidate passed the complete automated test suite on
Windows/Python 3.12:

`140 passed`

A separately curated PHASE demonstration in the public software release records
its machine-validation and human-review provenance explicitly.