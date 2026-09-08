# FAIRagro / DFFP v3.4.0 changes

## Structured quantitative values

- Added `QuantitativeMetricValue` to validation metrics and tuning parameters.
- Preserves the authored display string while storing exact `Decimal` estimates, bounds, nominal levels, unit codes, aggregation, labeled points, and uncertainty components.
- Added conservative deterministic migration for scalar, range, estimate-with-range, plus/minus, percentage, and nonnumeric summary forms.
- Added consistency checks between the display and structured representations.

## Completeness and provenance

- Added whole-table target-row completeness after a multi-target quantitative result table is selected.
- Extended deterministic repair consolidation to restore missing atomic target/metric cells from source candidates without inventing values or unsupported units.
- Added related-resource identifier object type and verification status.
- Added unknown source-item and item/modality mismatch checks.
- Constrained evidence pages, SHA-256 hashes, and representation confidence at schema level.

## Reproducibility, review, and exports

- Active contracts are `extraction_v10`, `repair_v2`, and `fairagro-dffp-v3.4.0`; the composed prompts retain the frozen v3.2.8 instructions and append versioned refinements.
- Added `DFFP_RUN_PURPOSE=test|evaluation|publication`; publication is blocked for test runs.
- Human metric corrections refresh structured quantitative values, and new schema fields can be added through audited correction patches.
- Excel exports now provide dedicated quantitative metric, related resource, and semantic check sheets.
- Extraction manifests include a machine-readable quality summary at export time.
