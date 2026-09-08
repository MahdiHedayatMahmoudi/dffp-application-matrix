# v3.5.0 changes

- Preserve central statistics and reported ranges together as
  `estimate_with_range` structured values.
- Inject `schema_version`, canonical-PDF SHA-256, and source-bundle SHA-256 into
  extraction provenance after the LLM response.
- Normalize Windows-style source-package manifest paths for portable reuse.
- Surface authored limitation/risk sections as deterministic qualitative
  guardrail candidates and run focused repair when coverage is weak.
- Require direct evidence for identifiers marked `source_explicit`.
- Serialize structured decimal values as JSON numbers while keeping exact
  authored strings in `value_or_summary` and `as_reported`.
- Export a top-level-array JSON Schema and a minimal RO-Crate JSON-LD descriptor
  for validated application matrices.
- Enforce the semantic gate automatically for publication runs.
