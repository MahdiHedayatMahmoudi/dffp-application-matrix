from models import (
    DecisionRiskProfile,
    EvidenceRecord,
    EvidenceType,
    EvaluationSupport,
    FAIRagroApplicationDataFitnessModel,
    MetricContext,
    MetricScope,
    OutputsAndFitnessIndicators,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)
from semantic_postprocess import postprocess_semantics


def ev(text: str):
    return [EvidenceRecord(claim="Supported", evidence_type=EvidenceType.explicit, source_text=text)]


def test_interval_terminology_is_normalized_but_source_text_is_preserved():
    record = FAIRagroApplicationDataFitnessModel(
        decision_risk_profile=DecisionRiskProfile(
            critical_quality_dimensions=["Prediction-interval calibration"]
        ),
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="PICP",
                    value_or_summary="0.90",
                    context=MetricContext.cross_validated_interval_assessment,
                    scope=MetricScope.dataset_family,
                    scope_label="all crops",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=ev("Prediction-interval calibration is assessed separately; PICP 0.90."),
                )
            ]
        ),
    )
    out = postprocess_semantics(record)
    assert out.decision_risk_profile.critical_quality_dimensions == ["prediction-interval assessment"] or out.decision_risk_profile.critical_quality_dimensions == ["Prediction-interval assessment"]
    assert "Prediction-interval calibration" in out.validation_and_diagnostics.validation_metrics[0].evidence[0].source_text


def test_held_out_interval_metric_materializes_generic_support_limitation():
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="PICP",
                    value_or_summary="0.90",
                    context=MetricContext.cross_validated_interval_assessment,
                    scope=MetricScope.dataset_family,
                    scope_label="all crops",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=ev("PICP 0.90 at held-out stations"),
                )
            ]
        )
    )
    out = postprocess_semantics(record)
    assert any("unobserved locations" in x.lower() for x in out.validation_and_diagnostics.validation_limitations)


def test_raster_document_gets_raster_specific_held_out_limitation():
    from models import DatasetCharacteristics, DatasetSpatialCharacteristics
    record = FAIRagroApplicationDataFitnessModel(
        dataset_characteristics=DatasetCharacteristics(
            spatial=DatasetSpatialCharacteristics(support_or_unit="1 km raster grid cell"),
            variables_or_layers=["prediction raster"],
        ),
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="PICP", value_or_summary="0.90", context=MetricContext.cross_validated_interval_assessment,
                scope=MetricScope.dataset, scope_label="dataset", evaluation_support=EvaluationSupport.held_out_station,
                evidence=ev("PICP 0.90 at held-out stations"),
            )
        ]),
    )
    out = postprocess_semantics(record)
    assert any("not all raster/grid cells" in x.lower() for x in out.validation_and_diagnostics.validation_limitations)
