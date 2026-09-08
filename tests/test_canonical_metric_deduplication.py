from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

from metric_postprocess import derive_fitness_metrics_from_canonical_ledger
from models import (
    AggregationStatistic,
    EvidenceRecord,
    EvidenceType,
    EvaluationSupport,
    FAIRagroApplicationDataFitnessModel,
    MetricContext,
    MetricScope,
    MetricValueKind,
    QuantitativeMetricValue,
    RepresentationMethod,
    SourceModality,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)
from quantitative_values import parse_quantitative_value
from repair_consolidation import consolidate_repair_record
from semantic_postprocess import postprocess_semantics


def _evidence(claim: str) -> list[EvidenceRecord]:
    return [EvidenceRecord(
        claim=claim,
        evidence_type=EvidenceType.explicit,
        source_section="3 Validation",
        source_location="Table 3",
        source_text=claim,
        source_page=7,
        source_item_id="tbl_003",
        source_modality=SourceModality.author_table,
        representation_method=RepresentationMethod.docling_structured,
    )]


def _metric(
    name: str,
    display: str,
    *,
    aggregation: AggregationStatistic = AggregationStatistic.unspecified,
    nominal_level: Decimal | None = None,
    range_basis: str | None = None,
    interpretation: str | None = None,
    population: str | None = None,
    claim: str | None = None,
) -> ValidationMetricRecord:
    q = parse_quantitative_value(display, metric_name=name, unit="days")
    # The extraction model may provide a fully structured value even when the
    # display string contains a written unit that the conservative parser does
    # not parse by itself. Mirror that real pipeline shape for this regression.
    if q.kind == MetricValueKind.text_summary and display.lower().endswith(" days"):
        numeric = display[:-5].strip()
        q = parse_quantitative_value(numeric, metric_name=name, unit="days")
        q.as_reported = display
    q.aggregation = aggregation
    q.nominal_level = nominal_level
    q.range_basis = range_basis
    return ValidationMetricRecord(
        name=name,
        value_or_summary=display,
        unit="days",
        quantitative_value=q,
        context=(
            MetricContext.cross_validated_interval_assessment
            if name == "MPIW"
            else MetricContext.out_of_sample_validation
        ),
        scope=MetricScope.crop,
        scope_label="Target A (101)",
        evaluation_support=EvaluationSupport.held_out_station,
        evaluation_population=population,
        interpretation=interpretation,
        evidence=_evidence(claim or f"{name} {display}"),
    )


def test_postprocess_collapses_scalar_display_variants_and_keeps_richer_metadata():
    rich = _metric(
        "MAE",
        "5.7 days",
        aggregation=AggregationStatistic.median,
        interpretation="Median MAE over produced surfaces.",
        population="Held-out station observations.",
        claim="Target A median MAE is 5.7 days.",
    )
    weak = _metric("MAE", "5.7", claim="Target A: MAE 5.7.")

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[rich, weak])
    )
    out = postprocess_semantics(record)
    metrics = out.validation_and_diagnostics.validation_metrics

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.value_or_summary == "5.7 days"
    assert metric.quantitative_value.numeric_value == Decimal("5.7")
    assert metric.quantitative_value.unit_code == "d"
    assert metric.quantitative_value.aggregation == AggregationStatistic.median
    assert metric.interpretation == "Median MAE over produced surfaces."
    assert metric.evaluation_population == "Held-out station observations."
    assert len(metric.evidence) == 2


def test_postprocess_collapses_range_variants_and_preserves_specific_range_basis():
    rich = _metric(
        "MAE range",
        "3.9-7.5",
        aggregation=AggregationStatistic.other,
        range_basis="Across per-phase median MAEs",
        interpretation="Range of per-phase median MAE values.",
    )
    weak = _metric(
        "MAE range",
        "3.9-7.5",
        range_basis="reported range (basis not stated)",
    )

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[weak, rich])
    )
    out = postprocess_semantics(record)
    metrics = out.validation_and_diagnostics.validation_metrics

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.quantitative_value.lower_bound == Decimal("3.9")
    assert metric.quantitative_value.upper_bound == Decimal("7.5")
    assert metric.quantitative_value.aggregation == AggregationStatistic.other
    assert metric.quantitative_value.range_basis == "Across per-phase median MAEs"
    assert metric.interpretation == "Range of per-phase median MAE values."


def test_postprocess_preserves_nominal_level_from_richer_interval_metric():
    rich = _metric(
        "MPIW",
        "23.5",
        aggregation=AggregationStatistic.median,
        nominal_level=Decimal("0.9"),
        range_basis="Width of nominal 90% prediction intervals",
    )
    weak = _metric("MPIW", "23.5")

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[weak, rich])
    )
    out = postprocess_semantics(record)
    metric = out.validation_and_diagnostics.validation_metrics[0]

    assert len(out.validation_and_diagnostics.validation_metrics) == 1
    assert metric.quantitative_value.nominal_level == Decimal("0.9")
    assert metric.quantitative_value.aggregation == AggregationStatistic.median
    assert metric.quantitative_value.range_basis == "Width of nominal 90% prediction intervals"


def test_conflicting_nominal_levels_are_not_deduplicated():
    ninety = _metric("MPIW", "23.5", nominal_level=Decimal("0.90"))
    ninety_five = _metric("MPIW", "23.5", nominal_level=Decimal("0.95"))

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[ninety, ninety_five])
    )
    out = postprocess_semantics(record)

    assert len(out.validation_and_diagnostics.validation_metrics) == 2


def test_conflicting_aggregations_are_not_deduplicated():
    median = _metric("MAE", "5.7", aggregation=AggregationStatistic.median)
    mean = _metric("MAE", "5.7", aggregation=AggregationStatistic.mean)

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[median, mean])
    )
    out = postprocess_semantics(record)

    assert len(out.validation_and_diagnostics.validation_metrics) == 2


def test_derived_fitness_view_uses_deduplicated_canonical_ledger():
    rich = _metric("MAE", "5.7", aggregation=AggregationStatistic.median)
    weak = _metric("MAE", "5.7")
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[rich, weak])
    )

    record = postprocess_semantics(record)
    derive_fitness_metrics_from_canonical_ledger(record)

    assert len(record.validation_and_diagnostics.validation_metrics) == 1
    assert len(record.outputs_and_fitness_indicators.fitness_for_use_metrics.producer_side_metrics) == 1


def test_structured_backfill_does_not_add_display_only_duplicate():
    existing = _metric(
        "MAE",
        "5.7 days",
        aggregation=AggregationStatistic.median,
        claim="Target A median MAE is 5.7 days.",
    )
    initial = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[])
    )
    repaired = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[existing])
    )
    bundle = SimpleNamespace(metric_candidate_hints=[{
        "kind": "structured_table_metric",
        "source_item_id": "tbl_003",
        "target": "Target A (101)",
        "metric_name": "MAE",
        "metric_family": "MAE",
        "value": "5.7",
        "source_page": 7,
        "source_location": "Table 3",
        "section_hint": "3 Validation",
    }])

    out, report = consolidate_repair_record(initial, deepcopy(repaired), bundle)

    assert len(out.validation_and_diagnostics.validation_metrics) == 1
    assert out.validation_and_diagnostics.validation_metrics[0].value_or_summary == "5.7 days"
    assert report.structured_candidates_backfilled == 0
