from decimal import Decimal
from types import SimpleNamespace

from models import (
    AggregationStatistic,
    EvidenceRecord,
    EvidenceType,
    EvaluationSupport,
    FAIRagroApplicationDataFitnessModel,
    MetricContext,
    MetricScope,
    RepresentationMethod,
    SourceModality,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)
from quantitative_values import parse_quantitative_value
from repair_consolidation import (
    consolidate_repair_record,
    enrich_structured_metric_semantics,
)


CAPTION = (
    "Table 3. Out-of-sample accuracy and prediction-interval performance of the groups "
    "(all error metrics in days). MAE and RMSE are medians over all produced surfaces; "
    "the MAE range spans its per-phase median MAEs. PICP and MPIW are the median empirical "
    "coverage of the nominal 90 % prediction intervals and their median width."
)


def _ev(target: str, metric: str, value: str) -> list[EvidenceRecord]:
    text = f"{target}: {metric} {value}."
    return [EvidenceRecord(
        claim=text,
        evidence_type=EvidenceType.explicit,
        source_section="3 Validation",
        source_location="Table 3",
        source_text=text,
        source_page=7,
        source_item_id="tbl_003",
        source_modality=SourceModality.author_table,
        representation_method=RepresentationMethod.docling_structured,
    )]


def _metric(
    name: str,
    value: str,
    target: str,
    *,
    unit: str | None = None,
    aggregation: AggregationStatistic = AggregationStatistic.unspecified,
    nominal_level: Decimal | None = None,
    unit_code: str | None = None,
    range_basis: str | None = None,
) -> ValidationMetricRecord:
    q = parse_quantitative_value(value, metric_name=name, unit=unit)
    q.aggregation = aggregation
    q.nominal_level = nominal_level
    q.unit_code = unit_code or q.unit_code
    q.range_basis = range_basis or q.range_basis
    return ValidationMetricRecord(
        name=name,
        value_or_summary=value,
        unit=unit,
        quantitative_value=q,
        context=(
            MetricContext.cross_validated_interval_assessment
            if name in {"PICP", "MPIW"}
            else MetricContext.out_of_sample_validation
        ),
        scope=MetricScope.crop,
        scope_label=target,
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=_ev(target, name, value),
    )


def _hints() -> list[dict]:
    values = {
        "Target A": {"MAE": "5.7", "MAE range": "3.9-7.5", "RMSE": "7.1", "PICP": "0.898", "MPIW": "23.5"},
        "Target B": {"MAE": "5.4", "MAE range": "3.8-7.5", "RMSE": "6.8", "PICP": "0.896", "MPIW": "22.6"},
    }
    return [
        {
            "kind": "structured_table_metric",
            "source_item_id": "tbl_003",
            "target": target,
            "metric_name": name,
            "metric_family": "MAE" if name == "MAE range" else name,
            "column": name,
            "value": value,
            "source_page": 7,
            "source_location": "Table 3",
            "section_hint": "3 Validation",
            "caption": CAPTION,
        }
        for target, metrics in values.items()
        for name, value in metrics.items()
    ]


def _bundle() -> SimpleNamespace:
    return SimpleNamespace(metric_candidate_hints=_hints())


def test_existing_structured_rows_inherit_agreed_same_table_semantics():
    rich = _metric(
        "PICP",
        "0.898",
        "Target A",
        unit="proportion",
        aggregation=AggregationStatistic.median,
        nominal_level=Decimal("0.9"),
        unit_code="1",
        range_basis="Empirical coverage of nominal 90% prediction intervals",
    )
    weak = _metric("PICP", "0.896", "Target B", unit="proportion")
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[rich, weak])
    )

    changed = enrich_structured_metric_semantics(record, _bundle())
    out = record.validation_and_diagnostics.validation_metrics[1]

    assert changed == 1
    assert out.quantitative_value.aggregation == AggregationStatistic.median
    assert out.quantitative_value.nominal_level == Decimal("0.9")
    assert out.quantitative_value.unit_code == "1"
    assert out.quantitative_value.range_basis == "Empirical coverage of nominal 90% prediction intervals"


def test_caption_enriches_mae_range_basis_and_median_without_guessing_from_column_name():
    metric = _metric("MAE range", "3.9-7.5", "Target A", unit="days")
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[metric])
    )

    changed = enrich_structured_metric_semantics(record, _bundle())
    q = metric.quantitative_value

    assert changed == 1
    assert q.aggregation == AggregationStatistic.median
    assert q.range_basis == "Across per-phase median MAEs"
    assert q.unit_code == "d"


def test_caption_enriches_picp_and_mpiw_even_without_rich_prototype():
    picp = _metric("PICP", "0.896", "Target B", unit="proportion")
    mpiw = _metric("MPIW", "22.6", "Target B", unit="days")
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[picp, mpiw])
    )

    changed = enrich_structured_metric_semantics(record, _bundle())

    assert changed == 2
    assert picp.quantitative_value.aggregation == AggregationStatistic.median
    assert picp.quantitative_value.nominal_level == Decimal("0.9")
    assert picp.quantitative_value.unit_code == "1"
    assert picp.quantitative_value.range_basis == "Empirical coverage of nominal 90% prediction intervals"
    assert mpiw.quantitative_value.aggregation == AggregationStatistic.median
    assert mpiw.quantitative_value.nominal_level == Decimal("0.9")
    assert mpiw.quantitative_value.unit_code == "d"
    assert mpiw.quantitative_value.range_basis == "Width of nominal 90% prediction intervals"


def test_conflicting_same_table_nominal_levels_are_not_propagated_without_caption_support():
    # No caption: conflicting explicit levels must not be copied into a weak sibling.
    hints = _hints()
    for hint in hints:
        hint["caption"] = "Table 3. PICP values by target."
    bundle = SimpleNamespace(metric_candidate_hints=hints)
    a = _metric("PICP", "0.89", "Target A", unit="proportion", nominal_level=Decimal("0.90"), unit_code="1")
    b = _metric("PICP", "0.88", "Target B", unit="proportion", nominal_level=Decimal("0.95"), unit_code="1")
    c = _metric("PICP", "0.87", "Target C", unit="proportion")
    # Target C does not need to exist in hints for group consensus: it still points to the same authored item.
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[a, b, c])
    )

    enrich_structured_metric_semantics(record, bundle)

    assert c.quantitative_value.nominal_level is None


def test_backfilled_siblings_receive_caption_semantics_during_consolidation():
    initial = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[])
    )
    repaired = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            _metric("MAE", "5.7", "Target A", unit="days", aggregation=AggregationStatistic.median),
            _metric("RMSE", "7.1", "Target A", unit="days", aggregation=AggregationStatistic.median),
            _metric("PICP", "0.898", "Target A", unit="proportion", aggregation=AggregationStatistic.median, nominal_level=Decimal("0.9"), unit_code="1"),
            _metric("MPIW", "23.5", "Target A", unit="days", aggregation=AggregationStatistic.median, nominal_level=Decimal("0.9"), unit_code="d"),
        ])
    )

    out, report = consolidate_repair_record(initial, repaired, _bundle())
    target_b = {m.name: m for m in out.validation_and_diagnostics.validation_metrics if m.scope_label == "Target B"}

    assert report.structured_candidates_backfilled == 6
    assert report.structured_semantics_enriched >= 5
    assert target_b["MAE"].quantitative_value.aggregation == AggregationStatistic.median
    assert target_b["RMSE"].quantitative_value.aggregation == AggregationStatistic.median
    assert target_b["MAE range"].quantitative_value.range_basis == "Across per-phase median MAEs"
    assert target_b["PICP"].quantitative_value.nominal_level == Decimal("0.9")
    assert target_b["PICP"].quantitative_value.unit_code == "1"
    assert target_b["MPIW"].quantitative_value.nominal_level == Decimal("0.9")


def test_backfill_does_not_copy_target_specific_population_across_targets():
    target_a_mae = _metric(
        "MAE",
        "5.7",
        "Target A",
        unit="days",
        aggregation=AggregationStatistic.median,
    )
    target_a_mae.evaluation_population = "Held-out stations across 231 produced phase-year surfaces"
    target_a_rmse = _metric(
        "RMSE",
        "7.1",
        "Target A",
        unit="days",
        aggregation=AggregationStatistic.median,
    )
    target_a_rmse.evaluation_population = "Held-out stations across 231 produced phase-year surfaces"
    target_a_picp = _metric(
        "PICP",
        "0.898",
        "Target A",
        unit="proportion",
        aggregation=AggregationStatistic.median,
        nominal_level=Decimal("0.9"),
        unit_code="1",
    )
    target_a_picp.evaluation_population = "Five-fold held-out stations over produced surfaces"
    target_b_mae = _metric("MAE", "5.4", "Target B", unit="days")

    initial = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[])
    )
    repaired = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[target_a_mae, target_a_rmse, target_a_picp, target_b_mae]
        )
    )

    out, _report = consolidate_repair_record(initial, repaired, _bundle())
    target_b = {
        m.name: m
        for m in out.validation_and_diagnostics.validation_metrics
        if m.scope_label == "Target B"
    }

    assert target_b["MAE range"].evaluation_population is None
    assert target_b["RMSE"].evaluation_population is None
    # Generic protocol wording may be shared because it does not encode a target count.
    assert target_b["PICP"].evaluation_population == "Five-fold held-out stations over produced surfaces"


def test_revalidation_clears_legacy_cross_target_population_but_preserves_same_target_support():
    explicit_a = _metric("MAE", "5.7", "Target A", unit="days")
    explicit_a.evaluation_population = "Held-out stations across 231 produced phase-year surfaces"
    explicit_a.evidence[0].claim = "Target A median MAE is 5.7 days."
    explicit_a.evidence[0].source_text = "Target A: MAE 5.7."

    synthetic_a = _metric("RMSE", "7.1", "Target A", unit="days")
    synthetic_a.evaluation_population = "Held-out stations across 231 produced phase-year surfaces"

    synthetic_b = _metric("RMSE", "6.8", "Target B", unit="days")
    synthetic_b.evaluation_population = "Held-out stations across 231 produced phase-year surfaces"

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[explicit_a, synthetic_a, synthetic_b]
        )
    )

    changed = enrich_structured_metric_semantics(record, _bundle())

    assert changed >= 1
    assert synthetic_a.evaluation_population == "Held-out stations across 231 produced phase-year surfaces"
    assert synthetic_b.evaluation_population is None
