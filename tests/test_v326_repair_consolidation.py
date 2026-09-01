from models import (
    AnalysisCharacteristics,
    AnalysisType,
    DataQualityDependencies,
    EvidenceRecord,
    EvidenceType,
    EvaluationSupport,
    FAIRagroApplicationDataFitnessModel,
    MetricContext,
    MetricScope,
    UncertaintyHandling,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)
from repair_consolidation import consolidate_repair_record
from semantic_checks import validate_record
from source_bundle import SourceBundle


def ev(text: str, section: str = "3 Results") -> list[EvidenceRecord]:
    return [EvidenceRecord(claim=text, evidence_type=EvidenceType.explicit, source_text=text, source_section=section)]


def metric(name, value, context, scope, label, support=EvaluationSupport.held_out_station, text=None):
    return ValidationMetricRecord(
        name=name, value_or_summary=value, context=context, scope=scope, scope_label=label,
        evaluation_support=support, evidence=ev(text or f"{name} {value}"),
    )


def test_consolidation_preserves_initial_atomic_metrics_and_adds_repair_metrics():
    initial = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(analysis_type=[AnalysisType.regression, AnalysisType.remote_sensing]),
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            metric("MAE", "5.2", MetricContext.out_of_sample_validation, MetricScope.dataset_family, "All groups"),
            metric("RMSE", "6.5", MetricContext.out_of_sample_validation, MetricScope.dataset_family, "All groups"),
            metric("PICP", "0.89", MetricContext.cross_validated_interval_assessment, MetricScope.subgroup, "Group A"),
            metric("MPIW", "23.5", MetricContext.cross_validated_interval_assessment, MetricScope.subgroup, "Group A"),
            metric("BSE median", "0.7 to 1.3", MetricContext.spatial_uncertainty_summary, MetricScope.variable_or_layer, "Layer X", EvaluationSupport.spatial_surface),
        ]),
        data_quality_dependencies=DataQualityDependencies(uncertainty_handling=UncertaintyHandling()),
    )
    repaired = FAIRagroApplicationDataFitnessModel(
        # Repair removes an application-only analysis type; consolidation must not re-union it.
        analysis_characteristics=AnalysisCharacteristics(analysis_type=[AnalysisType.regression]),
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            metric("accuracy", "MAE 5.2 and RMSE 6.5", MetricContext.out_of_sample_validation, MetricScope.dataset_family, "All groups", text="All groups MAE 5.2 and RMSE 6.5"),
            metric("PICP", "0.91", MetricContext.cross_validated_interval_assessment, MetricScope.crop_phase, "Target stage (18)"),
            metric("MPIW", "16.0", MetricContext.cross_validated_interval_assessment, MetricScope.variable_or_layer, "Target stage 18"),
            metric("QSI", "0.84", MetricContext.downstream_use_case, MetricScope.use_case, "Demonstrated Site B", EvaluationSupport.use_case),
            metric("standard_deviation", "SD-threshold multipliers 1.0, 1.5, 2.0 were evaluated", MetricContext.selection_or_tuning, MetricScope.model_or_method, "Method A", EvaluationSupport.observation_station),
        ]),
        data_quality_dependencies=DataQualityDependencies(uncertainty_handling=UncertaintyHandling()),
    )

    out, report = consolidate_repair_record(initial, repaired)
    rows = {(m.name, m.value_or_summary, m.context.value) for m in out.validation_and_diagnostics.validation_metrics}
    assert ("MAE", "5.2", "out_of_sample_validation") in rows
    assert ("RMSE", "6.5", "out_of_sample_validation") in rows
    assert ("PICP", "0.89", "cross_validated_interval_assessment") in rows
    assert ("MPIW", "23.5", "cross_validated_interval_assessment") in rows
    assert ("BSE median", "0.7 to 1.3", "spatial_uncertainty_summary") in rows
    assert ("QSI", "0.84", "downstream_use_case") in rows
    assert not any(m.name == "accuracy" for m in out.validation_and_diagnostics.validation_metrics)
    assert not any(m.name == "standard_deviation" for m in out.validation_and_diagnostics.validation_metrics)
    assert out.validation_and_diagnostics.tuning_parameters
    assert "threshold" in out.validation_and_diagnostics.tuning_parameters[0].name.lower()
    assert AnalysisType.remote_sensing not in out.analysis_characteristics.analysis_type
    assert report.composite_metrics_demoted == 1
    assert report.tuning_parameters_moved == 1


def test_consolidation_harmonizes_same_target_domain_scope_and_derives_interval_view():
    initial = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[]),
        data_quality_dependencies=DataQualityDependencies(uncertainty_handling=UncertaintyHandling()),
    )
    repaired = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            metric("MAE", "3.9", MetricContext.out_of_sample_validation, MetricScope.crop_phase, "Crop heading phase (18)"),
            metric("PICP", "0.90", MetricContext.cross_validated_interval_assessment, MetricScope.variable_or_layer, "Crop heading phase 18"),
            metric("MPIW", "16.0", MetricContext.cross_validated_interval_assessment, MetricScope.variable_or_layer, "Crop heading phase (18)"),
        ]),
        data_quality_dependencies=DataQualityDependencies(uncertainty_handling=UncertaintyHandling()),
    )
    out, report = consolidate_repair_record(initial, repaired)
    interval = [m for m in out.validation_and_diagnostics.validation_metrics if m.name in {"PICP", "MPIW"}]
    assert interval and all(m.scope == MetricScope.crop_phase for m in interval)
    products = out.data_quality_dependencies.uncertainty_handling.products_or_measures
    assert {p.name for p in products} == {"PICP", "MPIW"}
    assert report.scopes_harmonized == 2
    assert report.uncertainty_views_added == 2


def test_composite_metric_is_semantic_error_before_consolidation():
    rec = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            metric("accuracy", "MAE 5.2 and RMSE 6.5", MetricContext.out_of_sample_validation, MetricScope.dataset_family, "All groups")
        ])
    )
    bundle = SourceBundle(
        package_dir="/tmp/pkg", source_filename="paper.pdf", source_sha256="abc",
        source_fidelity_status="pass", source_fidelity_initial_status="pass", source_fidelity_summary={},
        unresolved_items=[], clean_markdown_path="/tmp/paper.md", text_for_llm="source", bundle_sha256="def",
        item_counts={}, source_item_registry={}, metric_candidate_hints=[], metric_candidate_focus=[],
    )
    issues = validate_record(rec, bundle)
    assert any(x.code == "COMPOSITE_CANONICAL_METRIC_RECORD" and x.severity == "error" for x in issues)


def test_structured_candidate_backfills_missing_selected_sibling_without_paper_specific_rule():
    from types import SimpleNamespace
    initial = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[]),
        data_quality_dependencies=DataQualityDependencies(uncertainty_handling=UncertaintyHandling()),
    )
    repaired = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            metric("NSE", "0.81", MetricContext.out_of_sample_validation, MetricScope.model_or_method, "Method A", text="Method A NSE 0.81"),
            metric("KGE", "0.76", MetricContext.out_of_sample_validation, MetricScope.model_or_method, "Method B", text="Method B KGE 0.76"),
        ]),
        data_quality_dependencies=DataQualityDependencies(uncertainty_handling=UncertaintyHandling()),
    )
    # Give both existing records the same structured source locator.
    for m in repaired.validation_and_diagnostics.validation_metrics:
        m.evidence[0].source_item_id = "tbl_001"
        m.evidence[0].source_page = 4
        m.evidence[0].source_location = "Table 1"
    bundle = SimpleNamespace(metric_candidate_hints=[
        {"kind": "structured_table_metric", "source_item_id": "tbl_001", "target": "Method A", "metric_name": "NSE", "value": "0.81", "source_page": 4, "source_location": "Table 1", "section_hint": "3 Validation"},
        {"kind": "structured_table_metric", "source_item_id": "tbl_001", "target": "Method A", "metric_name": "KGE", "value": "0.77", "source_page": 4, "source_location": "Table 1", "section_hint": "3 Validation"},
        {"kind": "structured_table_metric", "source_item_id": "tbl_001", "target": "Method B", "metric_name": "KGE", "value": "0.76", "source_page": 4, "source_location": "Table 1", "section_hint": "3 Validation"},
    ])
    out, report = consolidate_repair_record(initial, repaired, bundle)
    assert any(m.name == "KGE" and m.scope_label == "Method A" and m.value_or_summary == "0.77" for m in out.validation_and_diagnostics.validation_metrics)
    assert report.structured_candidates_backfilled == 1
