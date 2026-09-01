# FAIRagro DFFP pipeline v3.2.5

v3.2.5 is a document-agnostic cross-modal completeness and semantic-repair refinement. It is designed to improve heterogeneous scientific-paper extraction without hard-coding the PHASE manuscript or any other paper.

## Main changes

- Active extraction prompt: `extraction_v9`; conditional repair prompt: `repair_v1`; manifest schema version: `fairagro-dffp-v3.2.5`.
- Prose metric discovery uses paragraph plus sentence/adjacent-sentence windows so summary/fine-grained quantitative evidence is less likely to be lost.
- Short metric aliases are token-bounded to prevent false matches inside unrelated words.
- A compact high-priority candidate block highlights current-source summary quality/uncertainty and demonstrated downstream numerical results.
- Prose completeness matching prioritizes scientific target compatibility and supports parent/subsection source-location differences.
- Guarded analysis-type postprocessing now covers application-domain leakage such as remote-sensing analysis as well as model calibration, while retaining labels when producer workflow evidence actually supports them.
- Optional one-pass semantic repair runs only when deterministic ERROR-level checks remain. It receives the current full record, exact errors and original source bundle, returns the same strict Pydantic model, and is revalidated normally.
- Manifest records initial extraction, semantic repair run(s), repair failure if any, and final structured run.
- New environment controls: `DFFP_SEMANTIC_REPAIR`, `DFFP_SEMANTIC_REPAIR_MAX_ATTEMPTS`, `DFFP_SEMANTIC_REPAIR_PROMPT_VERSION`.

## Generalization contract

No document-specific crop names, geographic names, metric values, table/figure IDs, section numbers or use-case values are coded into these rules. Common metric aliases are normalization/discovery aids only. Paper-specific metric names are preserved when explicitly reported by the current source.

## Verification

- Full lightweight test suite: **67 passed**.
- Regression checks include generic table metrics, prose/adjacent-sentence metric discovery, downstream custom metrics, guarded remote-sensing/calibration analysis labels, deterministic source candidate ordering, and conditional semantic repair orchestration.
