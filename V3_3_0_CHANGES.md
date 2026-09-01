# FAIRagro DFFP v3.3.0 — Human review and publication workflow

v3.3.0 does **not** change the frozen automatic extraction contract. The package
continues to use `system_v2`, `extraction_v9`, `repair_v1`, and the
`fairagro-dffp-v3.2.8` extraction schema. The new functionality is a deterministic,
LLM-free review layer around a strict-pass machine-generated matrix.

## New review workflow

- `review_workflow.py` provides deterministic review IDs, immutable machine-baseline
  handling, decision history, reviewed-matrix derivation, and a publication gate.
- `run_review.py` exposes the workflow on the command line.
- `review_app.py` provides a small Streamlit interface for item-by-item human review.
- Review provenance is stored in `review_manifest.json`; the original
  `application_matrix.json`, extraction manifest, and validation report are copied
  into the review workspace as immutable machine audit artifacts.

## Stable review IDs

Review decisions never depend on list positions such as
`validation_metrics[7]`. Stable IDs are generated from normalized scientific identity
fields and source locators for:

- canonical validation metrics;
- related resources;
- tuning parameters; and
- evidence records.

Deterministic fitness-metric mirrors are excluded from the queue because they are
regenerated from the canonical metric ledger after review.

## Review actions

Each review item supports:

- `verify` — accept the machine extraction as source-supported;
- `correct` — apply a schema-valid human correction while retaining the original
  machine baseline and decision history;
- `reject` — remove an unsupported canonical item/evidence claim from the reviewed
  derivative; and
- `defer` — leave the item machine-extracted pending later author/final-manuscript
  review.

Evidence records become `human_verified` or `human_corrected` only through this
human-review layer. The extraction model is still forcibly limited to
`machine_extracted`.

## Publication gate

The deterministic publication gate requires, by default:

1. zero machine semantic errors;
2. zero machine semantic warnings;
3. completion of every required review item with no pending/deferred item;
4. an explicitly frozen source manuscript;
5. an explicitly checked source-document identifier; and
6. a resolved source-document DOI.

The DOI requirement can be relaxed explicitly for repositories/publication contexts
where a source DOI legitimately does not exist, but it is not relaxed by default.
The final publication filename is supplied explicitly by the user; the generic
pipeline does not hard-code `PHASE` or any other project name.

## Output-63 regression

The v3.3.0 review workflow was initialized against the strict-pass v3.2.8 output 63:

- 15 canonical validation metrics;
- 6 related resources;
- 1 tuning parameter; and
- 44 unique evidence claims;
- 66 total review items.

An unreviewed derivation remains schema-valid and the publication gate stays closed.
A functional all-verified smoke test (not a scientific human review) demonstrated that
all duplicate appearances of one reviewed evidence identity are promoted consistently,
fitness mirrors are regenerated, the reviewed record becomes `human_verified`, and the
publication gate can emit a stable final JSON only after the source-publication state is
explicitly satisfied.
