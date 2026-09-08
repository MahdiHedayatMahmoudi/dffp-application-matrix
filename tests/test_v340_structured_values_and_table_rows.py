from decimal import Decimal
import json
from types import SimpleNamespace

from models import (
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
from exporters import export_results
from repair_consolidation import consolidate_repair_record
from semantic_checks import validate_record
from semantic_postprocess import postprocess_semantics
from source_bundle import SourceBundle


def _evidence(target: str) -> list[EvidenceRecord]:
    return [EvidenceRecord(
        claim=f"{target} result is reported.",
        evidence_type=EvidenceType.explicit,
        source_text=f"{target} result is reported.",
        source_item_id="tbl_003",
        source_modality=SourceModality.author_table,
        representation_method=RepresentationMethod.docling_structured,
    )]


def _metric(name: str, value: str, target: str) -> ValidationMetricRecord:
    return ValidationMetricRecord(
        name=name,
        value_or_summary=value,
        unit="days",
        context=MetricContext.out_of_sample_validation,
        scope=MetricScope.crop,
        scope_label=target,
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=_evidence(target),
    )


def _hints():
    values = {
        "Target A": {"MAE": "1.1", "RMSE": "1.4"},
        "Target B": {"MAE": "2.1", "RMSE": "2.5"},
        "Target C": {"MAE": "3.1", "RMSE": "3.6"},
    }
    return [
        {
            "kind": "structured_table_metric",
            "source_item_id": "tbl_003",
            "target": target,
            "metric_name": name,
            "metric_family": name,
            "value": value,
            "source_page": 7,
            "source_location": "Table 3",
            "section_hint": "3 Validation",
        }
        for target, metrics in values.items()
        for name, value in metrics.items()
    ]


def _bundle():
    return SourceBundle(
        package_dir="/tmp/package",
        source_filename="paper.pdf",
        source_sha256="abc",
        source_fidelity_status="pass",
        source_fidelity_initial_status="pass",
        source_fidelity_summary={},
        unresolved_items=[],
        clean_markdown_path="/tmp/paper.md",
        text_for_llm="source",
        bundle_sha256="def",
        item_counts={},
        source_item_registry={
            "tbl_003": {
                "item_type": "table",
                "source_page": 7,
                "source_location": "Table 3",
                "section_hint": "3 Validation",
            }
        },
        metric_candidate_hints=_hints(),
    )


def test_estimate_with_range_is_exact_and_preserves_source_rendering():
    value = parse_quantitative_value("0.900 (0.888–0.920)", metric_name="PICP")
    assert value.as_reported == "0.900 (0.888–0.920)"
    assert value.numeric_value == Decimal("0.900")
    assert value.lower_bound == Decimal("0.888")
    assert value.upper_bound == Decimal("0.920")
    assert value.nominal_level is None


def test_legacy_metric_value_is_backfilled_without_changing_display_value():
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[_metric("PICP", "0.900 (0.888–0.920)", "Target A")]
        )
    )
    out = postprocess_semantics(record)
    metric = out.validation_and_diagnostics.validation_metrics[0]
    assert metric.value_or_summary == "0.900 (0.888–0.920)"
    assert metric.quantitative_value.numeric_value == Decimal("0.900")


def test_selected_multi_target_table_reports_missing_target_rows():
    record = postprocess_semantics(FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[_metric("MAE", "1.1", "Target A"), _metric("RMSE", "1.4", "Target A")]
        )
    ))
    issues = validate_record(record, _bundle())
    assert any(x.code == "STRUCTURED_TABLE_TARGET_ROWS_MISSING" and x.severity == "error" for x in issues)


def test_consolidation_backfills_every_metric_for_missing_table_targets():
    initial = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[])
    )
    repaired = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[_metric("MAE", "1.1", "Target A"), _metric("RMSE", "1.4", "Target A")]
        )
    )
    out, report = consolidate_repair_record(initial, repaired, SimpleNamespace(metric_candidate_hints=_hints()))
    rows = {(x.scope_label, x.name, x.value_or_summary) for x in out.validation_and_diagnostics.validation_metrics}
    assert rows == {
        ("Target A", "MAE", "1.1"), ("Target A", "RMSE", "1.4"),
        ("Target B", "MAE", "2.1"), ("Target B", "RMSE", "2.5"),
        ("Target C", "MAE", "3.1"), ("Target C", "RMSE", "3.6"),
    }
    assert report.structured_target_rows_backfilled == 2
    assert report.structured_candidates_backfilled == 4
    assert all(x.quantitative_value is not None for x in out.validation_and_diagnostics.validation_metrics if x.scope_label != "Target A")


def test_structured_value_conflict_with_display_is_an_error():
    metric = _metric("MAE", "1.1", "Target A")
    metric.quantitative_value = QuantitativeMetricValue(
        kind=MetricValueKind.scalar,
        as_reported="1.1",
        numeric_value=Decimal("9.9"),
    )
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[metric])
    )
    issues = validate_record(record, SourceBundle(
        package_dir="/tmp/package", source_filename="paper.pdf", source_sha256="abc",
        source_fidelity_status="pass", source_fidelity_initial_status="pass",
        source_fidelity_summary={}, unresolved_items=[], clean_markdown_path="/tmp/paper.md",
        text_for_llm="source", bundle_sha256="def", item_counts={},
    ))
    assert any(x.code == "QUANTITATIVE_VALUE_DISPLAY_CONFLICT" and x.severity == "error" for x in issues)


def test_export_has_comparison_ready_metric_sheet_and_quality_summary(tmp_path):
    record = postprocess_semantics(FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[_metric("PICP", "0.900 (0.888–0.920)", "Target A")]
        )
    ))
    paths = export_results(
        [record],
        {"semantic_checks": [{"severity": "info", "code": "NO_DETERMINISTIC_ISSUES"}]},
        output_dir=str(tmp_path),
    )
    import openpyxl
    workbook = openpyxl.load_workbook(paths.excel_path, read_only=True)
    assert workbook.sheetnames == [
        "records",
        "record_fields",
        "quantitative_metrics",
        "related_resources",
        "evidence",
        "semantic_checks",
    ]
    manifest = json.loads(open(paths.manifest_path, encoding="utf-8").read())
    assert manifest["quality_summary"]["structured_numeric_metric_count"] == 1
