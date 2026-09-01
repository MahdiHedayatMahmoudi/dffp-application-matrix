# FAIRagro / DFFP pipeline v3.2 — focused semantic refinement

This release keeps the v3.1 architecture and changes the schema/prompt/check layer so the next PHASE extraction can be compared cleanly against `application_matrix(54).json`.

## Implemented in this v3.2 package

- Replaced normalized prediction-interval `calibration` enums with `assessment` terminology.
- Split metric summary scope from evaluation support via `evaluation_support` and `evaluation_population`.
- Added optional `scope_label` for crop/phase/region-specific summaries.
- Separated uncertainty support scope from interval-assessment context.
- Added `model calibration` versus `prediction-interval assessment` analysis types.
- Added `prompts/extraction_v4.txt` with explicit PICP/MPIW coverage/width rules and held-out-support rules.
- Added deterministic semantic errors for deprecated interval-calibration terminology in normalized fields while excluding authored `source_text`/source locators.
- Added deterministic warnings when PICP/MPIW evaluation support is not stated.
- Added deterministic review-state enforcement: an extraction LLM cannot self-assign `human_verified` or `human_corrected`.
- Added regression tests for these rules.
- Made Docling optional at module-import time so pure caption/audit tests can run without Docling installed; actual ingestion still fails fast with a clear installation message.
- Fixed initial source-fidelity logic so `review` queue items prevent an incorrect `pass` status.
- Fixed incomplete-package resume provenance so cached Docling JSON no longer depends on an undefined `conv_res` object.
- Added `.env.example`; the clean release intentionally excludes `.env` and runtime source packages.

## Verification

`pytest -q` on the clean source package: **21 passed**.

The new raw terminology checker finds 7 reviewer-targeted deprecated normalized fields in `application_matrix(54).json`, demonstrating that v3.2 would no longer silently report `NO_DETERMINISTIC_ISSUES` for that class of problem.

## Intentionally deferred to the next stage

- Interactive/CLI author-review queue that promotes evidence from `machine_extracted` to `human_verified` / `human_corrected`.
- Strict publication-readiness gate for frozen manuscripts.
- Exact `source_quote` versus normalized `evidence_summary` split.
- Recovery cache key/freshness redesign and stronger `pass_after_recovery` status semantics.

These are best implemented after running the same updated PHASE manuscript through v3.2 once, so the schema/prompt effect can be measured cleanly as the next output (e.g. 55) versus 54.
