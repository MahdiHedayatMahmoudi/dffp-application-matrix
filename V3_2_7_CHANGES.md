# FAIRagro DFFP pipeline v3.2.7

v3.2.7 is a validator/consolidation hardening release. It keeps the extraction and repair prompts unchanged (`extraction_v9` + `repair_v1`) and addresses the concrete false positives and merge regressions exposed by the full v3.2.6 output manifest and validation report.

## Release configuration safety

- Prompt/schema versions are release-locked by default.
- Stale `DFFP_*_PROMPT_VERSION` / `DFFP_SCHEMA_VERSION` values from an older `.env` are ignored rather than silently downgrading a newer package.
- Set `DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE=true` only for an intentional experiment.
- The manifest records the effective release configuration and ignored stale overrides.

## Source-candidate precision

- Prose metric/value matching now requires an adjacent value or explicit result relation.
- Formula exponents/indices, enumerated method steps, nominal interval levels, citation fragments and reference-section noise are not promoted into quantitative result candidates.
- Target-hint extraction rejects method/citation phrases, accepts legitimate temporal targets, and aggregate matching handles simple plural variants.
- Repeated sentence/paragraph candidates produce at most one equivalent completeness error.

## Consolidation cleanup

- Dataset-family scope labels are reduced to the scientific target only.
- Structured sibling backfill distinguishes qualified statistics such as `MAE` and `MAE range`.
- Evidence lists are deduplicated by claim and source anchor.
- Numerical evidence for a dedicated child target is removed from a broader parent metric when it does not support the parent's value.
- Method definitions explicitly reporting no standalone numerical result are retained as evidence but removed from the canonical quantitative metric ledger.
- `interval calibration metrics/statistics` is normalized to interval-assessment wording outside literal source fields.

## Regression result

Using the v3.2.6 output-61 record together with the source registry and candidate hints from its manifest, the v3.2.7 deterministic cleanup resolves the previously reported 20-error pattern to zero deterministic errors in the local regression harness (without making a new LLM call). The next real run must still be executed with `--strict` to verify the complete release against the original source package.
