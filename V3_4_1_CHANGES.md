# FAIRagro / DFFP v3.4.1 changes

v3.4.1 is a compatibility hotfix for OpenAI Structured Outputs. It does not
remove the structured quantitative representation introduced in v3.4.0.

## Fixed

- Keeps exact `Decimal` validation in Python while advertising quantitative
  fields as JSON Schema `number` values to the API.
- Removes Pydantic's automatically generated numeric-string regex branch,
  whose negative lookahead is rejected by OpenAI Structured Outputs.
- Preserves `as_reported` for the paper's original formatting, precision, and
  display text. The structured numeric fields remain available for validation,
  comparison, filtering, and later JSON-LD / RO-Crate mapping.
- Adds a startup schema-compatibility preflight. A future unsupported
  lookaround now stops the run before PDF ingestion and multimodal recovery.
- Adds regression tests for API-facing numeric schemas, local `Decimal`
  validation, and fail-fast detection of unsupported regex lookaround.

## Reusing an existing source package

The source package and successful visual-recovery results are independent of
this schema fix. Pass the existing package directory to `run_pipeline.py`; PDF
ingestion and multimodal recovery do not need to be repeated.
