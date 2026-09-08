# v3.5.1 changes

- Normalize incomplete quantitative shapes before SDK Pydantic parsing can
  reject the entire structured response.
- Downgrade `estimate_with_range` to `range` when no central value is supplied,
  or to `scalar` when only the central value is supplied.
- Reparse conservative numeric `text_summary` values during deterministic
  post-processing.
- Add extraction and repair prompt rules requiring every quantitative `kind` to
  match its supplied components.
- Retain all v3.5.0 source identity, qualitative completeness, schema, RO-Crate,
  and publication-gating improvements.
