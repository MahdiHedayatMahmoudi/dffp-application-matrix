# Changelog

This file preserves the internal development history of the FAIRagro / DFFP provenance-aware pipeline.

The public software release and internal pipeline identifiers are intentionally separate:

- Public software release: `v1.1.0`
- Current pipeline architecture: `v3.3.2`
- Frozen extraction schema: `fairagro-dffp-v3.2.8`

The `v3.x` entries below document internal engineering revisions and should not be interpreted as separate GitHub/Zenodo releases.

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

## v3.2.8 maintenance freeze

v3.2.8 keeps `system_v2`, `extraction_v9`, and `repair_v1` unchanged. It is a deterministic maintenance release intended to freeze the automatic extraction layer after a strict-pass extraction. Recovered authored captions are now accepted as valid visual-recovery provenance, and the broad `machine learning` analysis label requires explicit producer-workflow evidence rather than statistical modelling/interpolation alone. Schema version: `fairagro-dffp-v3.2.8`.

## v3.3.0 human review and publication workflow

v3.3.0 keeps the automatic extraction layer frozen at `system_v2` + `extraction_v9`
+ `repair_v1` with extraction schema `fairagro-dffp-v3.2.8`. Human review is an
independent deterministic layer and does not call the LLM.

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
python run_review.py migrate .\review
python run_review.py audit .\revie
python run_review.py conflicts .\review
python run_review.py derive .\review
python run_review.py gate .\review
```

`audit` is intentionally separate from `conflicts`: conflicts are structural
review-dependency contradictions that block derivation; audit issues are review
provenance-quality problems that block publication but leave draft derivation
available so legacy workspaces can be inspected and repaired.
