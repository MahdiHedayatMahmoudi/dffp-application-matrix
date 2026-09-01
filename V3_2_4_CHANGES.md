# FAIRagro DFFP pipeline v3.2.4

v3.2.4 generalizes the completeness/provenance refinements so they can be reused across heterogeneous scientific papers rather than encoding fixes for one manuscript.

## Cross-document changes

- Active prompt: `extraction_v8`; schema manifest version: `fairagro-dffp-v3.2.4`.
- Added generic `MetricScope` values for datasets, subgroups, variables/layers, sites/studies, experiments/treatments, models/methods and spatial/temporal subsets; domain-specific crop scopes remain backwards compatible.
- Generic structured-table metric discovery recognizes metric-like headers beyond MAE/RMSE/PICP/MPIW/BSE. Candidate hints never inject values or fitness judgments.
- Generic prose discovery recognizes common scientific quality metrics plus explicit paper-specific abbreviation/value pairs (for example `INDEX = value`) and summary/downstream quantitative statements.
- Selected structured-table targets must preserve supported metric-like sibling columns losslessly.
- Summary-level quantitative quality/uncertainty prose and demonstrated-application numerical results are checked for omission using current-source candidate hints.
- `ValidationMetricRecord.scope` is enforced as scientific target/aggregation; physical/statistical support belongs in `evaluation_support`.
- Unsupported `model calibration` is removed when the producer workflow has no calibration/parameter-estimation evidence.
- Source registry headings are cleaned conservatively for obvious PDF line-number artefacts.
- `source_item_id` is treated as modality-specific. Authored prose cannot inherit a nearby structured item locator; structured table/figure/formula/caption evidence still receives deterministic locator injection.
- Spatial applicability propagation reuses a limitation already present in the extracted record instead of synthesizing geography/domain-specific text.
- Held-out-observation interval limitations use generic wording unless the current dataset is explicitly raster/grid based.

## Non-hard-coding principle

No document-specific crop names, place names, numeric metric values, table/figure numbers, section numbers, or use-case values are introduced by the v3.2.4 extraction/postprocessing logic. Known metric aliases are used only for normalization/discovery; unfamiliar metric-like names remain supported as paper-specific metrics.
