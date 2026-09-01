from __future__ import annotations

from copy import deepcopy

from models import (
    EvaluationSupport,
    FAIRagroApplicationDataFitnessModel,
    MetricContext,
    MetricScope,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
    EvidenceRecord,
    EvidenceType,
    SourceModality,
    RepresentationMethod,
)
from repair_consolidation import consolidate_repair_record
from semantic_checks import validate_record
from semantic_postprocess import postprocess_semantics
from settings import Settings, RELEASE_EXTRACTION_PROMPT_VERSION, RELEASE_SCHEMA_VERSION
from source_bundle import SourceBundle, _extract_quantitative_pairs, _extract_target_hint


def _ev(claim: str, text: str, section: str = "3 Results", item: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        claim=claim,
        evidence_type=EvidenceType.explicit,
        source_section=section,
        source_location="Table 1" if item else section,
        source_text=text,
        source_item_id=item,
        source_modality=SourceModality.author_table if item else SourceModality.authored_prose,
        representation_method=RepresentationMethod.docling_structured if item else RepresentationMethod.docling_clean_markdown,
    )


def _bundle(hints: list[dict] | None = None) -> SourceBundle:
    return SourceBundle(
        package_dir=".", source_filename="paper.pdf", source_sha256="x",
        source_fidelity_status="pass", source_fidelity_initial_status="pass",
        source_fidelity_summary={}, unresolved_items=[], clean_markdown_path="paper.md",
        text_for_llm="", bundle_sha256="b", item_counts={}, source_item_registry={},
        metric_candidate_hints=hints or [], metric_candidate_focus=[],
    )


def test_release_lock_ignores_stale_prompt_schema_env(monkeypatch):
    monkeypatch.setenv("DFFP_EXTRACTION_PROMPT_VERSION", "extraction_v3")
    monkeypatch.setenv("DFFP_SCHEMA_VERSION", "fairagro-dffp-v3.1")
    monkeypatch.delenv("DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE", raising=False)
    s = Settings.from_env()
    assert s.extraction_prompt_version == RELEASE_EXTRACTION_PROMPT_VERSION
    assert s.schema_version == RELEASE_SCHEMA_VERSION
    assert s.ignored_release_config_overrides == {
        "DFFP_EXTRACTION_PROMPT_VERSION": "extraction_v3",
        "DFFP_SCHEMA_VERSION": "fairagro-dffp-v3.1",
    }


def test_release_lock_can_be_overridden_explicitly(monkeypatch):
    monkeypatch.setenv("DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE", "true")
    monkeypatch.setenv("DFFP_EXTRACTION_PROMPT_VERSION", "extraction_v8")
    monkeypatch.setenv("DFFP_SCHEMA_VERSION", "experimental-schema")
    s = Settings.from_env()
    assert s.extraction_prompt_version == "extraction_v8"
    assert s.schema_version == "experimental-schema"
    assert s.ignored_release_config_overrides == {}


def test_prose_pair_parser_rejects_formula_exponents_and_enumerated_mentions():
    formula = "A nominal interval is z sqrt(SE 2 i + sigma 2 prod), where SE i is the fold-specific standard error."
    method = "1. shift the bounds by one BSE, and 2. accumulate precipitation spread."
    assert _extract_quantitative_pairs(formula) == []
    assert _extract_quantitative_pairs(method) == []


def test_prose_pair_parser_keeps_actual_results_and_range():
    text = "Across seven phases, RMSE; median 7.1 days. The per-crop median PICP lies between 0.896 and 0.901."
    pairs = _extract_quantitative_pairs(text)
    keys = {(x["metric_key"], x["value"]) for x in pairs}
    assert ("rmse", "7.1") in keys
    assert ("picp", "0.896-0.901") in keys


def test_target_hint_rejects_citation_method_fragment_but_keeps_year_target():
    bad = "following the protocol for validating predictive uncertainty (Smith and Jones, 2023); results follow."
    good = "Figure 7. Derivation of the indicator for the Uckermark district in 2018, mirroring a prior example."
    assert _extract_target_hint(bad) is None
    assert _extract_target_hint(good) == "Uckermark district in 2018"


def test_dataset_family_scope_label_is_target_only_after_postprocess():
    metric = ValidationMetricRecord(
        name="MPIW", value_or_summary="18-24", unit="days",
        context=MetricContext.cross_validated_interval_assessment,
        scope=MetricScope.dataset_family,
        scope_label="Eight groups; per-group median interval widths",
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=[_ev("Widths are reported.", "Median interval widths are 18-24 days.")],
    )
    record = FAIRagroApplicationDataFitnessModel(validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[metric]))
    out = postprocess_semantics(record)
    assert out.validation_and_diagnostics.validation_metrics[0].scope_label == "Eight groups"


def test_structured_range_sibling_backfills_as_distinct_statistic():
    mae = ValidationMetricRecord(
        name="MAE", value_or_summary="5.7", unit="days",
        context=MetricContext.out_of_sample_validation,
        scope=MetricScope.subgroup, scope_label="Method A",
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=[_ev("Method A MAE is 5.7.", "Method A: MAE 5.7.", item="tbl_001")],
    )
    hints = [
        {"kind":"structured_table_metric","metric_name":"MAE","metric_key":"mae","target":"Method A","value":"5.7","source_item_id":"tbl_001","source_location":"Table 1","source_page":1,"section_hint":"3 Results"},
        {"kind":"structured_table_metric","metric_name":"MAE range","metric_key":"mae_range","target":"Method A","value":"3.9-7.5","source_item_id":"tbl_001","source_location":"Table 1","source_page":1,"section_hint":"3 Results"},
    ]
    record = FAIRagroApplicationDataFitnessModel(validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[mae]))
    merged, report = consolidate_repair_record(record, deepcopy(record), _bundle(hints))
    vals = {(m.name, m.value_or_summary) for m in merged.validation_and_diagnostics.validation_metrics}
    assert ("MAE range", "3.9-7.5") in vals
    assert report.structured_candidates_backfilled == 1


def test_cross_target_evidence_removed_and_non_outcome_definition_demoted():
    parent = ValidationMetricRecord(
        name="PICP", value_or_summary="0.898", unit="proportion",
        context=MetricContext.cross_validated_interval_assessment,
        scope=MetricScope.subgroup, scope_label="Group A",
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=[
            _ev("Group A PICP is 0.898.", "Group A PICP is 0.898."),
            _ev("Group A stage B PICP is 0.900.", "For Group A stage B, PICP is 0.900."),
        ],
    )
    child = ValidationMetricRecord(
        name="PICP", value_or_summary="0.900", unit="proportion",
        context=MetricContext.cross_validated_interval_assessment,
        scope=MetricScope.variable_or_layer, scope_label="Group A stage B",
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=[_ev("Group A stage B PICP is 0.900.", "For Group A stage B, PICP is 0.900.")],
    )
    definition = ValidationMetricRecord(
        name="standard error", value_or_summary="SE_i, fold-specific standard error",
        context=MetricContext.cross_validated_interval_assessment,
        scope=MetricScope.model_or_method, scope_label="Five-fold model",
        evaluation_support=EvaluationSupport.held_out_station,
        interpretation="Method-defined quantity; no standalone numerical summary is reported.",
        evidence=[_ev("SE is derived for held-out cases.", "Standard errors are derived for held-out cases.", "2 Methods")],
    )
    rec = FAIRagroApplicationDataFitnessModel(validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[parent, child, definition]))
    merged, report = consolidate_repair_record(rec, deepcopy(rec), _bundle())
    metrics = merged.validation_and_diagnostics.validation_metrics
    p = next(m for m in metrics if m.scope_label == "Group A")
    assert len(p.evidence) == 1
    assert all(m.name != "standard error" for m in metrics)
    assert report.cross_target_evidence_removed == 1
    assert report.non_outcome_metrics_demoted == 1


def test_interval_calibration_representation_is_normalized_but_source_text_is_not():
    metric = ValidationMetricRecord(
        name="PICP", value_or_summary="0.9", unit="proportion",
        context=MetricContext.cross_validated_interval_assessment,
        scope=MetricScope.dataset_family, scope_label="All groups",
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=[_ev("Interval calibration metrics are reported.", "The source says interval calibration metrics.")],
    )
    rec = FAIRagroApplicationDataFitnessModel(validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[metric]))
    # Put the machine-normalized phrase in a field touched by semantic normalization.
    metric.interpretation = "Interval calibration metrics"
    out = postprocess_semantics(rec)
    m = out.validation_and_diagnostics.validation_metrics[0]
    assert m.interpretation == "interval-assessment metrics" or m.interpretation == "Interval-assessment metrics"
    assert "interval calibration metrics" in m.evidence[0].source_text.lower()
