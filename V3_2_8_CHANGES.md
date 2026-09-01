# FAIRagro DFFP v3.2.8 maintenance changes

v3.2.8 freezes the extraction contract from v3.2.7 (`system_v2`, `extraction_v9`, `repair_v1`) and makes only deterministic maintenance changes.

## Changes

- Visual-recovery provenance validation now accepts `author_caption` as a valid authored modality when `representation_method=structured_visual_recovery`. The authored scientific modality and the technical recovery representation remain separate concepts.
- `analysis_type="machine learning"` is now guarded like other application-leakage-prone labels. It is retained only when the producer workflow contains explicit generic machine-learning method evidence (for example neural networks, random forests, gradient boosting, SVMs, kNN, decision trees, named ML frameworks, or explicit machine/deep/supervised/unsupervised learning language). Statistical regression/interpolation terminology alone does not trigger the label.
- Release schema version is `fairagro-dffp-v3.2.8`.
- Extraction and repair prompts are unchanged from v3.2.7.

These rules are document-agnostic and do not contain paper-specific crops, places, values, sections, tables, figures, or use cases.
