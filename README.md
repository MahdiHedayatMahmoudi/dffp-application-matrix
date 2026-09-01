# FAIRagro / DFFP provenance-aware scientific extraction pipeline

This project integrates the two workflows into one scientific pipeline:

1. **Scientific PDF ingestion / source-fidelity audit** using Docling with the original PDF as the canonical source.
2. **Selective structured multimodal recovery** of tables, formulas and scientifically valuable figures when the Docling representation is weak.
3. **Provenance-aware source bundling** that keeps authored scientific evidence separate from machine representations/transcriptions.
4. **Structured FAIRagro / DFFP extraction** through the OpenAI Responses API using a strict Pydantic-derived JSON schema.
5. **Deterministic semantic checks** for known high-risk mistakes such as invented fitness thresholds or unassessed categorical fitness judgments.
6. **JSON / manifest / validation / Excel / HTML exports** and a Streamlit extraction UI.
7. **LLM-free human review and publication workflow** with immutable machine baselines, stable review IDs, auditable decisions, reviewed-matrix derivation, and a deterministic publication gate.

## Architecture

```text
canonical PDF
    |
    v
scientific_pdf_ingestion.py
    |-- original.pdf
    |-- docling/document.clean.md
    |-- docling/document.docling.json
    |-- docling/document.doctags.txt
    |-- tables/*
    |-- figures/*
    |-- formulas/*
    |-- audit/source_fidelity.json
    `-- audit/recovery_queue.json
              |
              v
multimodal_recovery.py   (optional/selective)
              |
              v
source_bundle.py
              |
              v
llm_service.py + models.py + prompts/*
              |
              v
semantic_checks.py
              |
              v
application_matrix.json
extraction_manifest.json
validation_report.json
```

## Important evidence policy

The **PDF is canonical**. Clean Docling Markdown is a readable representation of authored prose. Structured table/formula/figure recovery is a machine transcription of an authored source modality. Generic AI picture descriptions are not accepted as authored scientific evidence and are disabled in ingestion by default.

`EvidenceRecord` therefore distinguishes:

- `evidence_type`: explicit / inferred / derived / assumed;
- `source_modality`: authored prose / table / figure / formula / caption;
- `representation_method`: Docling Markdown / Docling structured data / visual recovery / native PDF text.

This matters because an explicit value printed in an authored figure can still be **explicit scientific evidence** even though its textual representation was recovered by a VLM.

## Installation

Create a virtual environment and install:

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

pip install -U pip
pip install -r requirements.txt
```

Docling models may be downloaded on first use unless you provide a local artifacts path according to your Docling setup.

Copy the environment template:

```bash
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env`.

## One-command pipeline

```bash
python run_pipeline.py paper.pdf --output-dir outputs/phase
```

The default performs ingestion, queued high/medium structured recovery, DFFP extraction and exports.

Skip visual recovery:

```bash
python run_pipeline.py paper.pdf --no-recovery --output-dir outputs/phase_no_recovery
```

This is allowed, but unresolved conversion/recovery items are carried into provenance and generate semantic warnings.

## Separate scientific stages

### 1. Ingest only

```bash
python run_pipeline.py paper.pdf --ingest-only
```

or directly:

```bash
python scientific_pdf_ingestion.py --input-dir ./papers --output-root scientific_source_packages
```

### 2. Recover a source package

```bash
python run_pipeline.py scientific_source_packages/<package> --recover-only
```

or directly:

```bash
python multimodal_recovery.py \
  --package-dir scientific_source_packages/<package> \
  --priorities high,medium \
  --statuses required,recommended,review
```

### 3. Extract from an existing package

```bash
python run_pipeline.py scientific_source_packages/<package> --output-dir outputs/extraction
```

`<package>` means the **specific complete source-package subdirectory**, not the parent `scientific_source_packages` directory.
The selected directory must directly contain `source_package_manifest.json` and `audit/source_fidelity.json`.

If `scientific_source_packages` is beside `run_pipeline.py`, a Windows PowerShell example is:

```powershell
python run_pipeline.py .\scientific_source_packages\YOUR_PACKAGE_FOLDER --output-dir .\output_current --strict
```

This reuses the expensive Docling/visual-recovery work from the existing package. It rebuilds only the lightweight DFFP source bundle, runs the current extraction prompt/schema and deterministic postprocessing/checks, and writes the new extraction outputs.

For publication-oriented iteration, `--strict` is recommended. If deterministic semantic errors remain, the CLI exits with code 2 and writes `application_matrix.unvalidated.json` instead of a normal `application_matrix.json`, while still preserving the manifest and validation report for debugging.

By default v3.2.8 attempts one source-grounded semantic repair **only when ERROR-level deterministic checks remain**. The repair is then **deterministically consolidated with the initial extraction** rather than replacing it wholesale: corrected semantic fields from the repair remain authoritative, while source-supported canonical metrics and claim-level evidence from the initial extraction are preserved when the repair omitted them. Composite multi-metric summaries are demoted to section evidence once atomic metric records exist, tuning parameters are separated from validation metrics, and fitness views are rebuilt from the consolidated canonical ledger. `--strict` still fails if errors remain after consolidation. Disable repair with `DFFP_SEMANTIC_REPAIR=false` when you need a strictly single-call comparison.

This makes it easy to compare experimental conditions such as:

- old Markdown-only extraction;
- new schema/prompt with clean Markdown;
- provenance-aware source package without recovery;
- provenance-aware source package with structured recovery.

## Streamlit

```bash
streamlit run app_ui.py
```

The UI contains no hard-coded prompts or model names. Change models and prompt versions in `.env`.

### Local secrets

The clean source archive intentionally does **not** include `.env`. Copy `.env.example` to `.env` locally and add your API key there. Never commit or distribute the populated `.env`.

## Prompt versioning

Prompt files live under `prompts/`:

```text
prompts/system_v2.txt
prompts/extraction_v2.txt   # historical
prompts/extraction_v3.txt   # historical
prompts/extraction_v4.txt   # historical
prompts/extraction_v5.txt   # historical
prompts/extraction_v7.txt   # historical
prompts/extraction_v8.txt   # historical
prompts/extraction_v9.txt   # active
prompts/repair_v1.txt       # conditional semantic-repair prompt
```

The current active defaults are:

```text
DFFP_SYSTEM_PROMPT_VERSION=system_v2
DFFP_EXTRACTION_PROMPT_VERSION=extraction_v9
DFFP_SCHEMA_VERSION=fairagro-dffp-v3.2.8
DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE=false
```

The extraction manifest stores prompt hashes, model, schema version, source PDF hash, source-bundle hash, source-fidelity summary and token usage.

The manifest also records `semantic_repair_consolidations` when repair runs, including how many initial metrics were preserved, composite summaries demoted, tuning parameters separated, scopes harmonized and uncertainty views derived.

## Key changes from the earlier DFFP extractor

The schema now separates:

- `dataset_characteristics` from downstream `input_data_requirements`;
- `producer_workflow_inputs` from downstream requirements;
- published dataset gaps from upstream observation gaps;
- per-pixel metrics from `spatial_surface_summary` metrics;
- scientific evidence type from representation/transcription method;
- source-document DOI from related dataset/software/workflow identifiers.

Categorical fitness judgments remain `unassessed` unless the source or an explicit deterministic rule supports them.

## v3.2 semantic refinements

v3.2 focuses on publication-grade interpretation of validation and review provenance:

- prediction-interval evaluation is normalized as **assessment / coverage / width (sharpness)** rather than interval "calibration";
- `ValidationMetricRecord` separates metric summary `scope` from `evaluation_support` and `evaluation_population`;
- `UncertaintyEvidence` separates spatial/support `scope` from `assessment_context`;
- PICP/MPIW can therefore be represented as dataset/crop summaries that were evaluated at held-out stations without implying pixel-wise raster validation;
- deterministic semantic checks flag deprecated interval-calibration terminology in normalized fields while preserving authored wording inside `source_text`;
- the extraction model cannot self-assert `human_verified` or `human_corrected`: all LLM-produced evidence is deterministically reset to `machine_extracted`.

The next publication-stage review workflow can promote review statuses only after actual author checking.


## v3.2.1 metric-ledger refinements

v3.2.1 tightens the quantitative metric model exposed by v3.2:

- one canonical metric record now represents exactly one metric, target and aggregation scope;
- `MetricScope.crop_phase` represents crop-phase summaries across multiple years, while `phase_year` is reserved for a specific year;
- semantic checks flag semicolon/list-like scope labels and contradictions such as `scope=crop` with `scope_label=all crops`;
- `application_profile.beneficiaries` is now explicit-source only; inferred beneficiary groups remain null;
- `validation_and_diagnostics.validation_metrics` is the canonical quantitative metric ledger; producer-side and downstream fitness metric lists are derived deterministically from it after extraction, preventing duplicate values from drifting.

## v3.2.2 consolidation refinements

v3.2.2 combined the clean semantic behavior of v3.2 with the atomic metric ledger introduced in v3.2.1:

- atomic splitting is explicitly **lossless**: MAE/RMSE, PICP/MPIW and different targets from one source statement/table must each become separate records rather than being dropped during splitting;
- `dataset_family` with a specific crop label is a deterministic semantic error; crop and crop-phase summaries must use `crop` / `crop_phase`;
- every canonical quantitative metric requires at least one evidence record;
- downstream numerical use-case results remain in the canonical ledger and are deterministically routed to `application_specific_metrics`;
- a narrow postprocessor normalizes deprecated prediction-interval-calibration wording in machine-generated semantic fields while preserving authored `source_text`/locators verbatim;
- when interval metrics are explicitly evaluated at held-out stations, the validation limitations deterministically retain the corresponding “not all raster cells” caveat;
- `--strict` prevents a semantically invalid extraction from being emitted under the normal `application_matrix.json` filename.

## v3.2.3 completeness and provenance refinements

v3.2.3 keeps the v3.2.2 metric architecture and adds source-aware completeness/provenance controls:

- the source bundle now carries a deterministic source-item registry for tables, figures and formulas; page/item locators are injected after extraction rather than guessed by the model;
- conservative section hints are derived only from separate prose references to structured items, avoiding caption-adjacency mistakes such as assigning Table 3 to the wrong section;
- a quantitative metric-candidate index highlights explicit structured/prose metrics for completeness checking without turning them into fitness judgments;
- when a structured-table target is selected, supported MAE/RMSE/PICP/MPIW sibling metrics must be preserved losslessly;
- explicit numeric MPIW, BSE-summary and demonstrated downstream-use results can now trigger strict completeness errors when omitted;
- unsupported `analysis_type="model calibration"` is now an error when the producer workflow itself contains no calibration step;
- an already source-supported crop-occurrence/agricultural-land-use limitation is propagated into the spatial `known_scale_limitations` field;
- the active prompt is `extraction_v7` and the manifest schema version is `fairagro-dffp-v3.2.3`.


## v3.2.4 cross-document generalization refinements

v3.2.4 is intentionally a **document-agnostic** refinement rather than a patch for one manuscript:

- quantitative candidate discovery now recognizes generic metric-like table headers and explicit paper-specific name/value prose in addition to familiar metrics; no document-specific metric values, crops, places, figure numbers or section numbers are coded into the extraction logic;
- structured-table completeness is generic: once a target row is selected, metric-like sibling columns for that row must not silently disappear, including unfamiliar paper-specific metrics;
- summary-level quality/uncertainty metrics in authored prose/captions and numerical demonstrated-application results receive source-aware completeness checks so table extraction does not suppress other authored modalities;
- `MetricScope` now includes reusable scopes such as `dataset`, `subgroup`, `variable_or_layer`, `study_or_site`, `experiment_or_treatment`, `model_or_method`, and spatial/temporal subset scopes while retaining agricultural crop scopes for backwards compatibility;
- physical/statistical support is kept in `evaluation_support`; a named scientific target must not use a raster/station/support-like metric scope merely because the value was summarized over that support;
- source-item IDs are modality-specific: ordinary authored prose no longer inherits a nearby table/figure/formula locator, while true structured evidence still receives deterministic page/item metadata;
- obvious PDF line-number noise is removed conservatively from numbered section headings before source-registry mapping;
- unsupported `model calibration` is removed deterministically when the producer workflow contains no calibration/parameter-estimation step, preventing potential-use leakage even when strict mode is not used;
- spatial applicability propagation now reuses an already extracted source-supported limitation rather than generating geography/crop-specific wording;
- held-out-observation interval limitations are worded generically, with raster/grid wording used only when the current document actually describes raster/grid products;
- the active prompt is `extraction_v8` and the manifest schema version is `fairagro-dffp-v3.2.4`.

The active prompt explicitly states that examples are schema illustrations only and that entities, metrics, units, methods and use cases must come from the current paper.


## v3.2.5 cross-modal completeness and semantic repair

v3.2.5 keeps the document-agnostic v3.2.4 schema and strengthens extraction completeness without encoding any values, crops, places, figures, tables or section numbers from a particular manuscript:

- prose candidate discovery now uses focused sentence/adjacent-sentence windows in addition to paragraphs, improving capture of fine-grained validation, uncertainty and demonstrated-application results;
- known short metric aliases use token boundaries so abbreviations such as BSE, SE or p cannot be spuriously matched inside unrelated words;
- a compact **HIGH-PRIORITY QUANTITATIVE COMPLETENESS CANDIDATES** block brings source-derived summary quality/uncertainty and demonstrated numerical results to the model's attention while keeping the full candidate index available for audit;
- completeness checks match a named scientific target across compatible subsection/parent-section representations, so a coarser table result does not substitute for an explicitly reported finer-grained result;
- demonstrated downstream numerical results remain required canonical metrics with `context=downstream_use_case` and are routed deterministically to `application_specific_metrics`;
- analysis-type leakage protection is generalized for guarded controlled labels: for example, `remote-sensing analysis` is removed when remote-sensing inputs/methods do not occur in the producer workflow, while genuine remote-sensing papers retain it;
- when deterministic ERROR-level checks remain after the first extraction, the pipeline can perform **one focused semantic-repair call** using the current record, exact error list and original source bundle. Valid source-supported content is preserved; repair cannot bypass the strict Pydantic schema or final semantic checks;
- semantic repair is configurable with `DFFP_SEMANTIC_REPAIR`, `DFFP_SEMANTIC_REPAIR_MAX_ATTEMPTS` and `DFFP_SEMANTIC_REPAIR_PROMPT_VERSION`; set `DFFP_SEMANTIC_REPAIR=false` to force single-call extraction for controlled experiments;
- manifests record the initial extraction run, any repair run(s), repair failure (if any), and the final structured run separately for reproducibility;
- active extraction prompt: `extraction_v9`; conditional repair prompt: `repair_v1`; manifest schema version: `fairagro-dffp-v3.2.5`.

The semantic-repair call is not unconditional: a normal extraction that has no deterministic ERROR-level issues remains a single LLM extraction call. This preserves cost and experimental clarity while giving strict mode a source-grounded recovery path for genuine completeness/semantic omissions.

## v3.2.6 deterministic repair consolidation

v3.2.6 keeps the document-agnostic extraction/repair prompts from v3.2.5 and changes how a repair result is integrated:

- repair is treated as an additive/corrective patch rather than a wholesale replacement; corrected semantic classifications from the repair remain authoritative, while source-supported atomic metrics and claim-level evidence from the first extraction are retained when the repair omits them;
- exact duplicate canonical metrics are merged with their evidence; hierarchical targets remain independent;
- structured-table sibling values can be backfilled deterministically only when the current source already supplies enough prototypes to establish both the target scope and the metric context/support, avoiding document-specific metric assumptions;
- generic composite records that bundle multiple named metrics are removed from the canonical ledger once atomic records exist, while their supporting evidence is retained at section level; unresolved composite canonical records are a semantic error;
- quantitative workflow settings/parameters are separated into `validation_and_diagnostics.tuning_parameters` instead of being mixed with validation metrics;
- matching targets are scope-harmonized conservatively when a domain-specific target scope and only a generic fallback scope coexist;
- cross-validated interval metrics deterministically populate missing uncertainty-measure views;
- old derived fitness lists are discarded after consolidation and rebuilt from the final canonical metric ledger, preventing stale repaired copies from reintroducing duplicate or outdated scopes;
- manifests record `semantic_repair_consolidations` for auditability.

No paper-specific crop, geography, metric value, section number, table/figure ID or use-case value is encoded in these rules. Active extraction prompt remains `extraction_v9`, repair prompt remains `repair_v1`, and the schema version is `fairagro-dffp-v3.2.6`.

## v3.2.7 validator/consolidation hardening

v3.2.7 keeps `extraction_v9` and `repair_v1` unchanged and focuses on deterministic correctness revealed by a full output manifest + validation report:

- release prompt/schema versions are locked by default so a stale `.env` cannot silently make a new package run an older extraction contract; intentional experiments may set `DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE=true`;
- the manifest records the effective release configuration and any stale environment overrides that were ignored;
- prose metric discovery now requires an adjacent value or explicit result relation and rejects formula exponents, enumerated method steps, nominal-level mentions and citation/method fragments that previously created false completeness errors;
- target hints accept legitimate temporal targets but reject citation/protocol phrases and normalize simple plurals for aggregate-target matching;
- dataset-family `scope_label` values are normalized to target-only wording rather than mixing target and statistic description;
- structured sibling backfill distinguishes metric qualifiers such as `MAE` versus `MAE range`, so an already selected table row can preserve both without conflating them;
- repair evidence is deduplicated by claim + source anchor, child-target numerical evidence is removed from broader parent records when a dedicated child record exists, and method definitions with no standalone numerical outcome are demoted from the canonical metric ledger;
- normalized `interval calibration metrics/statistics` wording is converted to interval-assessment terminology while literal source quotations remain unchanged;
- the source-completeness validator deduplicates equivalent prose errors and uses result-bearing metric candidates rather than every nearby metric mention.

These rules are domain-agnostic and contain no manuscript-specific crop, place, metric value, table/figure ID or section number. Schema version: `fairagro-dffp-v3.2.7`.

## Multimodal recovery defaults

The integrated recovery executor was adjusted for this scientific use case:

- statuses: `required,recommended,review`;
- priorities: `high,medium`;
- formula recovery therefore runs by default rather than being skipped as medium priority;
- queue entries without a recoverable asset remain visible in fidelity provenance but are skipped by default bulk VLM execution rather than failing the whole run.

## Outputs

Typical extraction output directory:

```text
application_matrix.json
extraction_manifest.json
validation_report.json
application_matrix.xlsx
application_matrix.html
application_matrix_interactive.html
```

The source package separately retains the canonical PDF, Docling representations, visual assets, source-fidelity audit, recovery queue, recovery metadata and the generated `dffp/source_bundle.txt`.

## Tests

Tests that do not require Docling/model downloads can be run with:

```bash
pytest -q
```

## Recommended research workflow

For evaluation, preserve every generated source package and extraction manifest. Do not overwrite prompt versions used for reported results. Compare pipeline variants using fixed PDF, fixed model, fixed prompt version and fixed schema version whenever you want to attribute an improvement to one component.

## Troubleshooting: pandas 3 / interrupted ingestion

The ingestion code is compatible with both pandas 2.x and 3.x. In pandas 3,
`DataFrame.applymap()` was removed; the pipeline now uses a compatibility helper
that prefers `DataFrame.map()` and falls back for older pandas versions.

If Docling conversion finishes but a later export/audit step fails, **do not enable
"Rebuild existing source package" on the next run**. The pipeline will detect the
existing `docling/document.docling.json` and resume the incomplete package without
running the expensive PDF conversion again. Use rebuild/overwrite only when you
really want to discard the cached source package and reconvert the PDF.

Warnings about `strict_text` deprecation, disabled external Docling plugins, or
Transformers image-processing aliases are non-fatal unless followed by an actual
traceback.



## Prompt-version preflight and stale `.env` files

The current package ships `prompts/system_v2.txt`, historical `prompts/extraction_v2.txt`,
historical `prompts/extraction_v3.txt`/`prompts/extraction_v4.txt`/`prompts/extraction_v5.txt`/`prompts/extraction_v7.txt`/`prompts/extraction_v8.txt`, active `prompts/extraction_v9.txt`, and conditional `prompts/repair_v1.txt`. If you reused an older `.env`, update these lines:

```text
DFFP_SYSTEM_PROMPT_VERSION=system_v2
DFFP_EXTRACTION_PROMPT_VERSION=extraction_v9
DFFP_SCHEMA_VERSION=fairagro-dffp-v3.2.8
```

Restart Streamlit after editing `.env`, because settings are cached for the running process.
The pipeline now validates prompt files **before** starting expensive Docling conversion, so
a stale prompt version fails immediately with the available prompt versions listed.

If a previous run already produced `audit/source_fidelity.json`, rerunning the same PDF with
**Rebuild existing source package unchecked** reuses that source package. Structured visual
recovery is also cached by asset hash and prompt version, so successfully recovered items do
not need another model call.


## Structured Outputs schema compatibility

The extraction service prefers the official Python SDK Pydantic path:

```python
client.responses.parse(..., text_format=FAIRagroApplicationDataFitnessModel)
```

This avoids hand-maintaining JSON Schema details such as Pydantic `$ref` nodes.
A sanitized `responses.create` fallback remains for compatible older endpoints.
If your environment does not expose `responses.parse`, upgrade the SDK with
`python -m pip install -U openai`.


## Clean repository versus generated runtime data

The clean repository contains only pipeline source code, prompts, tests, configuration
templates and documentation. The following directories are generated at runtime and
should not be distributed as part of a clean source-code release:

```text
scientific_source_packages/
outputs/
archive_pre_v*/
__pycache__/
.pytest_cache/
```

Keep generated scientific source packages separately when they are needed for provenance,
reproducibility or extraction-only reruns. Do not commit `.env`; copy `.env.example` to
`.env` locally and add your own API key.

## v3.2.8 maintenance freeze

v3.2.8 keeps `system_v2`, `extraction_v9`, and `repair_v1` unchanged. It is a deterministic maintenance release intended to freeze the automatic extraction layer after a strict-pass extraction. Recovered authored captions are now accepted as valid visual-recovery provenance, and the broad `machine learning` analysis label requires explicit producer-workflow evidence rather than statistical modelling/interpolation alone. Schema version: `fairagro-dffp-v3.2.8`.

## v3.3.0 human review and publication workflow

v3.3.0 keeps the automatic extraction layer frozen at `system_v2` + `extraction_v9`
+ `repair_v1` with extraction schema `fairagro-dffp-v3.2.8`. Human review is an
independent deterministic layer and does not call the LLM.

### 1. Initialize a review workspace

Start from a strict-pass machine output and preserve all three audit artifacts:

```powershell
python run_review.py init .\output_63\application_matrix.json `
  --manifest .\output_63\extraction_manifest.json `
  --validation-report .\output_63\validation_report.json `
  --review-dir .\review_63 `
  --reviewer "Reviewer name"
```

This creates:

```text
review_63/
  application_matrix.machine.json
  extraction_manifest.machine.json
  validation_report.machine.json
  review_queue.json
  review_manifest.json
```

The machine baseline is hash-protected. Re-initializing the same workspace from a
different matrix is refused rather than overwriting review provenance.

### 2. Review items

For a command-line decision:

```powershell
python run_review.py decide .\review_63 <review_id> verify --reviewer "Reviewer name"
python run_review.py decide .\review_63 <review_id> reject --reviewer "Reviewer name" --reason "Unsupported by source"
python run_review.py decide .\review_63 <review_id> defer --reviewer "Reviewer name" --reason "Check final manuscript"
```

A correction uses a small JSON object containing only the fields that should change:

```powershell
python run_review.py decide .\review_63 <review_id> correct `
  --reviewer "Reviewer name" `
  --reason "Correct source-supported identifier" `
  --patch-file .\correction.json
```

Parent metric/resource/parameter corrections may not replace their `evidence` arrays;
review evidence records separately so scientific provenance cannot be silently rewritten.

For an interactive interface:

```powershell
streamlit run review_app.py
```

Enter the initialized review directory in the sidebar. The UI filters by priority,
item type and status, and records every verify/correct/reject/defer action in
`review_manifest.json`.

### 3. Derive the reviewed matrix

```powershell
python run_review.py derive .\review_63
```

This writes `application_matrix.reviewed.json` from the immutable machine baseline plus
review decisions. It never edits `application_matrix.machine.json`. Canonical metric
changes are propagated deterministically into the fitness metric views.

### 4. Publication state and gate

Do not mark a current draft as frozen merely to make the gate pass. After the source
manuscript is genuinely frozen and its source-document identifier has been checked:

```powershell
python run_review.py source-state .\review_final `
  --frozen `
  --identifier-checked `
  --source-doi "10.xxxx/final-article-doi"

python run_review.py gate .\review_final
```

The gate remains closed if machine validation has errors/warnings, required review is
pending/deferred, the manuscript is not frozen, or the source identifier is unresolved.

When the gate passes, choose the explicit project-specific stable filename:

```powershell
python run_review.py publish .\review_final `
  --output .\publication\PHASE_DFFP_application_matrix.json
```

The generic pipeline deliberately does not hard-code the PHASE filename. For another
paper or dataset, provide the appropriate stable publication name.

The recommended publication sequence remains: freeze the manuscript -> run the frozen
v3.2.8 extraction once -> strict 0/0 validation -> initialize a fresh review workspace ->
human review -> publication gate -> final matrix / RO-Crate linkage.

## v3.3.1 review evidence dependencies

v3.3.1 keeps the v3.2.8 automatic extractor frozen and hardens only the human
review layer. A retained canonical parent must not lose its final source-evidence
record through a child-evidence rejection. Use `python run_review.py conflicts
<review_dir>` to inspect contradictory decisions created by an older v3.3.0
workspace. Resolve a conflict by changing the evidence decision to Verify,
Correct, or Defer, or reject the unsupported parent record first.

Existing v3.3.0 review directories are compatible and should be reused rather
than reinitialized, so prior decisions and the immutable machine-baseline hash
are preserved.

## v3.3.2 review derivation hardening

v3.3.2 does not change automatic extraction. It fixes a human-review derivation
edge case where a rejected canonical validation metric could be promoted back
from an old fitness-metric mirror. During review derivation, fitness views are
now rebuilt strictly from the edited canonical ledger with legacy promotion
disabled.

Review decisions `Correct`, `Reject`, and `Defer` require a meaningful reason;
`Verify` may be recorded without a note. Correction patches must change at least
one machine-baseline field. Older review workspaces are migrated from the
immutable baseline and decision history to `fairagro-dffp-review-v1.2`; stale
derived artifacts are invalidated and regenerated.

For an existing v3.3.0/v3.3.1 workspace:

```powershell
python run_review.py migrate .\review_63
python run_review.py audit .\review_63
python run_review.py conflicts .\review_63
python run_review.py derive .\review_63
python run_review.py gate .\review_63
```

`audit` is intentionally separate from `conflicts`: conflicts are structural
review-dependency contradictions that block derivation; audit issues are review
provenance-quality problems that block publication but leave draft derivation
available so legacy workspaces can be inspected and repaired.
