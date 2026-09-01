# v3.2.1 changes

Focused refinement after PHASE application_matrix(55):

1. Added `MetricScope.crop_phase`.
2. Enforced one metric/target/scope per canonical metric record through prompt and deterministic checks.
3. Added checks for multi-target scope labels, all-crops/scope contradictions, and phase-year labels without a year.
4. Made plain beneficiary lists explicit-source only and added a check when provenance says beneficiaries were inferred.
5. Added `metric_postprocess.py`: fitness producer/application metric lists are deterministic views of the canonical validation metric ledger.
6. Activated `extraction_v5` and schema version `fairagro-dffp-v3.2.1`.
