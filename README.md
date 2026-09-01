# FAIRagro / DFFP provenance-aware scientific extraction pipeline

This project integrates the two workflows into one scientific pipeline:

1. **Scientific PDF ingestion / source-fidelity audit** using Docling with the original PDF as the canonical source.
2. **Selective structured multimodal recovery** of tables, formulas and scientifically valuable figures when the Docling representation is weak.
3. **Provenance-aware source bundling** that keeps authored scientific evidence separate from machine representations/transcriptions.
4. **Structured FAIRagro / DFFP extraction** through the OpenAI Responses API using a strict Pydantic-derived JSON schema.
5. **Deterministic semantic checks** for known high-risk mistakes such as invented fitness thresholds or unassessed categorical fitness judgments.
6. **JSON / manifest / validation / Excel / HTML exports** and a Streamlit extraction UI.
7. **LLM-free human review and publication workflow** with immutable machine baselines, stable review IDs, auditable decisions, reviewed-matrix derivation, and a deterministic publication gate.

## Release and pipeline versions

This repository uses separate public release and internal pipeline version identifiers.

- **Software release:** `v1.1.0`
- **Pipeline architecture:** `v3.3.2`
- **DFFP schema:** `fairagro-dffp-v3.2.8`
- **Extraction prompt:** `extraction_v9`
- **System prompt:** `system_v2`
- **Repair prompt:** `repair_v1`

The `v3.x` identifiers describe internal pipeline revisions. They are distinct from the GitHub/Zenodo software release version. Detailed revision history is preserved in [`CHANGELOG.md`](CHANGELOG.md) and the `V3_*_CHANGES.md` files.

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

## Current release configuration

Prompt files live under `prompts/`. The current release uses:

```text
prompts/system_v2.txt
prompts/extraction_v9.txt
prompts/repair_v1.txt
```

Historical extraction prompts remain in `prompts/` for reproducibility, but are not the active release configuration.

The current active defaults are:

```text
DFFP_SYSTEM_PROMPT_VERSION=system_v2
DFFP_EXTRACTION_PROMPT_VERSION=extraction_v9
DFFP_SCHEMA_VERSION=fairagro-dffp-v3.2.8
DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE=false
```

The extraction manifest stores prompt hashes, model, schema version, source PDF hash, source-bundle hash, source-fidelity summary and token usage. When semantic repair runs, the manifest also records repair/consolidation details for auditability.

## Key changes from the earlier DFFP extractor

The schema now separates:

- `dataset_characteristics` from downstream `input_data_requirements`;
- `producer_workflow_inputs` from downstream requirements;
- published dataset gaps from upstream observation gaps;
- per-pixel metrics from `spatial_surface_summary` metrics;
- scientific evidence type from representation/transcription method;
- source-document DOI from related dataset/software/workflow identifiers.

Categorical fitness judgments remain `unassessed` unless the source or an explicit deterministic rule supports them.

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

## Human review and publication workflow

The automatic extraction layer is frozen at `system_v2` + `extraction_v9` with extraction schema `fairagro-dffp-v3.2.8`. Human review is an independent deterministic layer and does not call the LLM.

### 1. Initialize a review workspace

Start from a strict-pass machine output and preserve all three audit artifacts:

```powershell
python run_review.py init .\output\application_matrix.json `
  --manifest .\output\extraction_manifest.json `
  --validation-report .\output\validation_report.json `
  --review-dir .\review `
  --reviewer "Reviewer name"
```

This creates:

```text
review/
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
python run_review.py decide .\review <review_id> verify --reviewer "Reviewer name"
python run_review.py decide .\review <review_id> reject --reviewer "Reviewer name" --reason "Unsupported by source"
python run_review.py decide .\review <review_id> defer --reviewer "Reviewer name" --reason "Check final manuscript"
```

A correction uses a small JSON object containing only the fields that should change:

```powershell
python run_review.py decide .\review <review_id> correct `
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
python run_review.py derive .\review
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

### Review integrity, migration, and derivation rules

A retained canonical parent must not lose its final source-evidence record through a child-evidence rejection. Use:

```powershell
python run_review.py conflicts <review_dir>
```

to inspect contradictory decisions. Resolve a conflict by changing the evidence decision to Verify, Correct, or Defer, or reject the unsupported parent record first.

Existing review directories should be migrated rather than reinitialized so prior decisions and the immutable machine-baseline hash are preserved.

During review derivation, fitness views are rebuilt strictly from the edited canonical ledger with legacy promotion disabled. This prevents a rejected canonical validation metric from being promoted back from an old fitness-metric mirror.

Review decisions `Correct`, `Reject`, and `Defer` require a meaningful reason; `Verify` may be recorded without a note. Correction patches must change at least one machine-baseline field. Older review workspaces can be migrated to the current review schema; stale derived artifacts are invalidated and regenerated.

For an existing workspace:

```powershell
python run_review.py migrate .\review
python run_review.py audit .\review
python run_review.py conflicts .\review
python run_review.py derive .\review
python run_review.py gate .\review
```

`audit` is intentionally separate from `conflicts`: conflicts are structural review-dependency contradictions that block derivation; audit issues are review-provenance quality problems that block publication but leave draft derivation available for inspection and repair.

## Reproducibility

For evaluation, preserve every generated source package and extraction manifest. Do not overwrite prompt versions used for reported results. Compare pipeline variants using a fixed PDF, model, prompt version, and schema version when attributing improvements to one component.

The repository includes:

- `requirements.txt` for supported dependency ranges;
- `requirements-lock-windows-py312.txt` for the tested Windows / Python 3.12 release environment;
- `tests/` for deterministic regression checks;
- `CHANGELOG.md` and `V3_*_CHANGES.md` for development history.

The `v1.1.0` release was smoke-tested with real PDF ingestion and the source-fidelity audit completed with `pass`.

## Development history

The current public software release is `v1.1.0`, corresponding to pipeline architecture `v3.3.2`.

Detailed implementation history is preserved in [`CHANGELOG.md`](CHANGELOG.md) and the `V3_*_CHANGES.md` files. These `v3.x` identifiers are internal pipeline revisions, not separate GitHub/Zenodo software releases.

## License

This software is released under the MIT License. See [`LICENSE`](LICENSE).
