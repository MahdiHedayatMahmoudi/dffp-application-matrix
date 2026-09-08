# v3.5.2 changes

- Fix duplicate prose-window target noise that produced a false
  `SUMMARY_PROSE_METRIC_UNREPRESENTED` error despite represented MAE/RMSE values.
- Add `revalidate_output.py` to regenerate validated exports from an existing
  matrix and extraction manifest without an LLM/API call.
- Replace oversized nested-object Excel cells with normalized summary,
  record-field, metric, resource, evidence, and check sheets.
- Split large scalar record fields into auditable chunks and protect all Excel
  cells from the 32,767-character limit.
- Retain v3.5.1 quantitative-shape resilience and all v3.5.0 publication
  grounding improvements.
