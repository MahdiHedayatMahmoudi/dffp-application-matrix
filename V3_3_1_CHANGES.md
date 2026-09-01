# FAIRagro DFFP v3.3.1 — Review evidence dependency maintenance

v3.3.1 is a maintenance release for the v3.3 human-review/publication layer.
It does not change the frozen automatic extraction contract (`system_v2` +
`extraction_v9`, machine schema `fairagro-dffp-v3.2.8`).

## Fixed

- Prevent a reviewer from rejecting the only source-evidence record of a retained
  canonical validation metric, related resource, or tuning parameter.
- Existing v3.3.0 workspaces containing such a contradictory decision are
  detected as an actionable `REJECTED_LAST_PARENT_EVIDENCE` review conflict
  before Pydantic/schema validation.
- The Streamlit UI catches dependency errors instead of surfacing an application
  traceback and shows a conflict-resolution panel.
- Added `python run_review.py conflicts <review_dir>` for deterministic conflict
  inspection.

## Review semantics

When evidence is the sole source support for a retained parent record:

- **Verify** it when it is correct.
- **Correct** it when the evidence wording/locator is wrong but the source support
  exists.
- **Defer** it when a decision is not yet possible.
- If the parent claim/metric itself is unsupported, **reject the parent record
  first**. The child evidence then becomes not applicable where appropriate.

Rejecting one evidence record remains allowed when another independent evidence
record still supports the retained parent.

## Compatibility

Existing v3.3.0 review workspaces can be reused; no re-extraction and no review
re-initialization is required. The latest review decision remains authoritative,
so a conflicting prior evidence rejection can be superseded by a new
Verify/Correct/Defer decision (or by rejecting the affected parent).

## Verification

- Full test suite: **92 passed**.
- Actual output-63 regression: 66 review items initialized with the same baseline
  SHA-256; rejecting the sole evidence of the all-crop MAE=5.2 metric is blocked
  before the decision is written.
