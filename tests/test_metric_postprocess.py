from models import (
    FAIRagroApplicationDataFitnessModel,
    FitnessForUseMetrics,
    MetricContext,
    MetricScope,
    OutputsAndFitnessIndicators,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
    EvidenceRecord,
    EvidenceType,
)
from metric_postprocess import derive_fitness_metrics_from_canonical_ledger


def metric(name: str, context: MetricContext):
    return ValidationMetricRecord(
        name=name,
        value_or_summary="1",
        context=context,
        scope=MetricScope.dataset_family,
        evidence=[EvidenceRecord(claim="Metric reported", evidence_type=EvidenceType.explicit, source_text=f"{name} 1")],
    )


def test_fitness_metrics_are_derived_from_canonical_ledger():
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                metric("MAE", MetricContext.out_of_sample_validation),
                metric("PICP", MetricContext.cross_validated_interval_assessment),
                metric("BSE median", MetricContext.spatial_uncertainty_summary),
                metric("Weather indicator", MetricContext.downstream_use_case),
                metric("Selection score", MetricContext.selection_or_tuning),
            ]
        ),
        outputs_and_fitness_indicators=OutputsAndFitnessIndicators(
            fitness_for_use_metrics=FitnessForUseMetrics(
                producer_side_metrics=[metric("BSE", MetricContext.spatial_uncertainty_summary)],
                application_specific_metrics=[metric("Second downstream metric", MetricContext.downstream_use_case)],
                formal_thresholds=["explicit formal threshold"],
            )
        ),
    )

    derive_fitness_metrics_from_canonical_ledger(record)
    ff = record.outputs_and_fitness_indicators.fitness_for_use_metrics
    assert [x.name for x in ff.producer_side_metrics] == ["MAE", "PICP", "BSE median"]
    assert [x.name for x in ff.application_specific_metrics] == ["Weather indicator", "Second downstream metric"]
    assert ff.formal_thresholds == ["explicit formal threshold"]
