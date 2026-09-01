# FAIRagro DFFP v3.2.2 changes

v3.2.2 is a consolidation release driven by the 55 -> 56 PHASE comparison. It is intended to preserve the strongest behavior of both outputs rather than redesign the pipeline.

## Metric ledger

- Atomic metric splitting is now explicitly lossless: when one source table/sentence reports multiple metrics or targets, every supported statistic must be retained as its own record.
- `dataset_family` is reserved for true family/all-dataset summaries. A specific crop must use `crop`; a crop-phase summary across years must use `crop_phase`.
- Every canonical quantitative metric requires source-grounded evidence.
- Downstream quantitative examples use `context=downstream_use_case` in the canonical ledger and are routed deterministically to `application_specific_metrics`.
- A semantic heuristic warns when evidence for one metric appears to contain another numeric sibling metric that has no atomic ledger record.

## Prediction-interval terminology

- Machine-generated normalized fields are deterministically normalized away from “prediction-interval calibration” toward assessment / coverage-and-width terminology.
- Authored `source_text`, `source_section`, and `source_location` are never rewritten.
- Held-out-station interval metrics deterministically materialize the validation limitation that the assessment is not across all raster cells.

## Analysis classification

- The extraction prompt explicitly prevents potential downstream crop-model calibration uses from leaking into the analysed workflow as `analysis_type=model calibration`.
- A deterministic warning flags `model calibration` when producer modeling/fitting text does not support an actual calibration step.

## Strict validation

CLI runs may use `--strict`. If deterministic semantic errors remain, the run exits with code 2 and writes `application_matrix.unvalidated.json` rather than `application_matrix.json`; the extraction manifest and validation report are still written for diagnosis.

The Streamlit path also labels JSON with semantic errors as `application_matrix.unvalidated.json`.

## Active versions

- extraction prompt: `extraction_v6`
- schema version: `fairagro-dffp-v3.2.2`
