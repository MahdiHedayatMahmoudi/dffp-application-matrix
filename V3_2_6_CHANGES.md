# FAIRagro DFFP pipeline v3.2.6

v3.2.6 is a deterministic repair-consolidation release. It keeps the source-discovery improvements of v3.2.5 while preventing a semantic-repair call from replacing a cleaner initial extraction with a richer but less structured record.

## Main changes

- Active extraction prompt remains `extraction_v9`; conditional repair prompt remains `repair_v1`; schema version is `fairagro-dffp-v3.2.6`.
- Semantic repair is now **additive/corrective rather than wholesale replacement**.
- The repaired record remains authoritative for corrected semantic classifications, so an invalid analysis label removed during repair is not reintroduced.
- Canonical quantitative metrics from the initial extraction are merged forward when the repair omits them.
- Exact duplicate metrics merge their evidence rather than creating duplicate canonical rows.
- When a structured table target is already selected, an omitted sibling can be deterministically reconstructed from the table candidate only if the same authored source already provides both a target-scope prototype and a same-metric context/support prototype elsewhere; this fills source-supported omissions without guessing metric semantics from a column name.
- Generic composite records that bundle multiple named metrics (for example a prose “accuracy” record containing MAE and RMSE) are removed from the canonical ledger once their atomic metrics exist; their source evidence is retained at section level.
- A deterministic semantic check rejects unresolved composite canonical metric records.
- Fine-grained target scopes are harmonized conservatively when the same normalized target has a domain-specific scope and a repaired metric uses only a generic fallback scope.
- Quantitative tuning parameters/settings are separated into `validation_and_diagnostics.tuning_parameters` instead of being treated as validation/performance metrics.
- Cross-validated interval-assessment metrics deterministically populate missing conceptual uncertainty-measure views, so a present interval-width metric is not silently absent from `products_or_measures`.
- Claim-level `evidence` arrays and source-related resources from the initial record are retained when a repair omits them.
- Previously derived producer/application fitness metric views are cleared after consolidation and rebuilt from the final canonical ledger, preventing stale repaired views from re-promoting duplicate or outdated metric records.
- The manifest records a `semantic_repair_consolidations` summary for auditability.

## Generalization contract

No paper names, crop names, regions, values, table/figure IDs, section numbers or use-case values are hard-coded. Consolidation uses schema roles, normalized metric/target identities, source evidence, metric context, evaluation support and generic parameter-language cues. Common metric aliases are used only to identify atomic metric families, not to inject source values.

## Verification

- Full lightweight test suite: **71 passed**.
- The consolidation logic was additionally exercised against the prior 59/60 outputs and produced the intended union: initial atomic family/crop/BSE metrics were preserved, repaired fine-grained/downstream metrics were added, composite summaries were demoted, the tuning parameter was separated, matching target scopes were harmonized, and the repaired analysis-type correction remained authoritative.
