# FAIRagro DFFP v3.3.2 — Human-review derivation hardening

v3.3.2 is a review-layer-only maintenance release. The automatic extraction
contract remains frozen at `fairagro-dffp-v3.2.8`, `system_v2`, `extraction_v9`,
and `repair_v1`.

## Fixed

- Rejected canonical validation metrics can no longer be re-promoted from stale
  `outputs_and_fitness_indicators.fitness_for_use_metrics` machine-baseline
  mirrors during human-review derivation.
- Fitness views are rebuilt strictly from the post-review canonical metric
  ledger; when that ledger is empty, stale fitness views are cleared.
- `Correct`, `Reject`, and `Defer` decisions now require a meaningful reason.
  `Verify` keeps an optional note.
- No-op correction patches are rejected. If the machine extraction is already
  correct, reviewers should use `Verify`.
- Older `fairagro-dffp-review-v1` and `v1.1` workspaces migrate to
  `fairagro-dffp-review-v1.2` from their immutable machine baseline while
  preserving decision history. Previously derived matrices and gate reports are
  invalidated and must be regenerated.
- The publication gate now checks review-decision provenance quality. Legacy
  decisions without required reasons or legacy no-op corrections remain
  derivable for audit purposes but block publication until superseded.

## New commands

```powershell
python run_review.py migrate .\review_63
python run_review.py audit .\review_63
```

`migrate` is also performed safely on first workspace load. `audit` reports
provenance-quality issues in the latest decision for each review item.
