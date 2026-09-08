from copy import deepcopy

from models import (
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
    MetricValueKind,
)
from quantitative_values import parse_quantitative_value
from semantic_postprocess import postprocess_semantics
from repair_consolidation import consolidate_repair_record


def _ev(claim: str, source_text: str, *, item: str = "tbl_003") -> EvidenceRecord:
    return EvidenceRecord(
        claim=claim,
        evidence_type=EvidenceType.explicit,
        source_section="3 Validation",
        source_location="Table 3",
        source_text=source_text,
        source_page=17,
        source_item_id=item,
        source_modality=SourceModality.author_table,
        representation_method=RepresentationMethod.docling_structured,
    )


def _metric(display: str, evidence: list[EvidenceRecord]) -> ValidationMetricRecord:
    q = parse_quantitative_value(display, metric_name="MAE", unit="days")
    if q.kind == MetricValueKind.text_summary and display.lower().endswith(" days"):
        q = parse_quantitative_value(display[:-5].strip(), metric_name="MAE", unit="days")
        q.as_reported = display
    return ValidationMetricRecord(
        name="MAE",
        value_or_summary=display,
        unit="days",
        quantitative_value=q,
        context=MetricContext.out_of_sample_validation,
        scope=MetricScope.crop,
        scope_label="Winter wheat (202)",
        evaluation_support=EvaluationSupport.held_out_station,
        evidence=evidence,
    )


def test_equivalent_canonical_metrics_collapse_paraphrased_evidence_from_same_source_fragment():
    source = "Winter wheat (202): MAE 5.7."
    rich = _metric(
        "5.7 days",
        [_ev("Winter wheat median out-of-sample MAE is 5.7 days.", source)],
    )
    weak = _metric(
        "5.7",
        [_ev("Winter wheat (202): MAE 5.7.", source)],
    )

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[rich, weak])
    )
    out = postprocess_semantics(record)
    metrics = out.validation_and_diagnostics.validation_metrics

    assert len(metrics) == 1
    assert len(metrics[0].evidence) == 1
    assert metrics[0].evidence[0].source_text == source
    assert "median out-of-sample" in metrics[0].evidence[0].claim


def test_same_source_anchor_with_distinct_source_fragments_is_not_collapsed():
    metric = _metric(
        "5.7 days",
        [
            _ev("Median MAE is 5.7 days.", "Winter wheat (202): MAE 5.7."),
            _ev("The range is 3.9-7.5 days.", "Winter wheat (202): MAE range 3.9-7.5."),
        ],
    )
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[metric])
    )
    out = postprocess_semantics(record)
    assert len(out.validation_and_diagnostics.validation_metrics[0].evidence) == 2


def test_same_source_fragment_at_different_source_items_is_not_collapsed():
    text = "MAE 5.7."
    metric = _metric(
        "5.7 days",
        [
            _ev("Table result.", text, item="tbl_003"),
            _ev("Independent table result.", text, item="tbl_004"),
        ],
    )
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[metric])
    )
    out = postprocess_semantics(record)
    assert len(out.validation_and_diagnostics.validation_metrics[0].evidence) == 2


def test_repair_consolidation_deduplicates_same_source_fragment_with_paraphrased_claim():
    source = "Winter wheat (202): MAE 5.7."
    initial = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[_metric("5.7 days", [_ev("Median MAE is 5.7 days.", source)])]
        )
    )
    repaired = deepcopy(initial)
    repaired.validation_and_diagnostics.validation_metrics[0].evidence = [
        _ev("Winter wheat (202): MAE 5.7.", source)
    ]

    out, report = consolidate_repair_record(initial, repaired)
    metric = out.validation_and_diagnostics.validation_metrics[0]
    assert len(metric.evidence) == 1
    assert report.evidence_records_preserved == 0
