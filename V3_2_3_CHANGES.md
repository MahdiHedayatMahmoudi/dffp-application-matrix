# FAIRagro DFFP pipeline v3.2.3

Focused completeness and provenance refinement after the PHASE output-57 comparison.

## Changes

- Added deterministic source-item registry to each LLM source bundle.
  - table/figure/formula item IDs now carry authoritative item location and page metadata;
  - conservative section hints are derived only from separate prose references, not from caption adjacency.
- Added deterministic quantitative metric-candidate hints from structured tables and authored prose.
- Added strict source-completeness checks for:
  - missing sibling metrics in a selected structured-table target;
  - missing MPIW when explicit numeric interval-width evidence accompanies extracted PICP;
  - missing numerical BSE summary when BSE is a core uncertainty product and the source explicitly quantifies it;
  - missing demonstrated downstream numerical result when a use-case section explicitly reports one.
- Structured evidence locators are normalized deterministically after extraction.
- Unsupported `analysis_type = model calibration` is now a semantic **error** rather than a warning.
- An already-supported crop-occurrence limitation is propagated into `dataset_characteristics.spatial.known_scale_limitations`.
- Default extraction prompt updated to `extraction_v7`; schema manifest version updated to `fairagro-dffp-v3.2.3`.
