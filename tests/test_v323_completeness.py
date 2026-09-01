from models import (
    FAIRagroApplicationDataFitnessModel,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
    MetricContext,
    MetricScope,
    EvidenceRecord,
    EvidenceType,
    DatasetCharacteristics,
    DatasetSpatialCharacteristics,
    LimitationsAndRisks,
)
from source_bundle import SourceBundle, _build_source_item_registry
from semantic_checks import validate_record
from semantic_postprocess import postprocess_semantics
from evidence_postprocess import normalize_evidence_locators


def ev(item_id: str | None = None, text: str = "All crops MAE 5.2"):
    from models import SourceModality, RepresentationMethod
    return [EvidenceRecord(
        claim="Metric is reported.", evidence_type=EvidenceType.explicit,
        source_text=text, source_item_id=item_id,
        source_modality=SourceModality.author_table if item_id else SourceModality.authored_prose,
        representation_method=RepresentationMethod.docling_structured if item_id else RepresentationMethod.docling_clean_markdown,
    )]


def bundle(*, registry=None, hints=None):
    return SourceBundle(
        package_dir="/tmp/package", source_filename="paper.pdf", source_sha256="abc",
        source_fidelity_status="pass", source_fidelity_initial_status="pass",
        source_fidelity_summary={}, unresolved_items=[], clean_markdown_path="/tmp/x.md",
        text_for_llm="source", bundle_sha256="def", item_counts={},
        source_item_registry=registry or {}, metric_candidate_hints=hints or [],
    )


def test_registry_maps_table_from_separate_prose_reference_not_caption_adjacency():
    md = """## 3.1 Previous section\n\nTable 3. Caption here.\n\n## 3.2 Accuracy across crops\n\nThe crop family is summarised in Table 3.\n"""
    fidelity = {"tables": [{"table_id": "tbl_003", "table_number": "3", "page_no": 16, "caption": "Table 3. Caption here."}]}
    reg = _build_source_item_registry(fidelity, md)
    assert reg["tbl_003"]["source_page"] == 16
    assert reg["tbl_003"]["source_location"] == "Table 3"
    assert reg["tbl_003"]["section_hint"] == "3.2 Accuracy across crops"


def test_evidence_locator_is_injected_from_registry():
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="MAE", value_or_summary="5.2", unit="days",
                context=MetricContext.out_of_sample_validation,
                scope=MetricScope.dataset_family, scope_label="all crops",
                evidence=ev("tbl_003"),
            )
        ])
    )
    reg = {"tbl_003": {"item_type": "table", "source_page": 16, "source_location": "Table 3", "section_hint": "3.2 Accuracy across crops"}}
    out = normalize_evidence_locators(record, reg)
    e = out.validation_and_diagnostics.validation_metrics[0].evidence[0]
    assert e.source_page == 16
    assert e.source_location == "Table 3"
    assert e.source_section == "3.2 Accuracy across crops"


def test_structured_selected_target_requires_supported_sibling_metrics():
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="MAE", value_or_summary="5.2", unit="days",
                context=MetricContext.out_of_sample_validation,
                scope=MetricScope.dataset_family, scope_label="all crops", evidence=ev("tbl_003"),
            ),
            ValidationMetricRecord(
                name="RMSE", value_or_summary="6.5", unit="days",
                context=MetricContext.out_of_sample_validation,
                scope=MetricScope.dataset_family, scope_label="all crops", evidence=ev("tbl_003", "All crops RMSE 6.5"),
            ),
            ValidationMetricRecord(
                name="PICP", value_or_summary="0.898", unit="proportion",
                context=MetricContext.cross_validated_interval_assessment,
                scope=MetricScope.dataset_family, scope_label="all crops", evidence=ev("tbl_003", "All crops PICP 0.898"),
            ),
        ])
    )
    hints = [
        {"kind": "structured_table_metric", "metric_family": fam, "target": "All crops", "value": val, "source_item_id": "tbl_003"}
        for fam, val in (("MAE", "5.2"), ("RMSE", "6.5"), ("PICP", "0.898"), ("MPIW", "21.3"))
    ]
    issues = validate_record(record, bundle(hints=hints))
    assert any(x.code == "STRUCTURED_TABLE_METRIC_SIBLINGS_MISSING" and x.severity == "error" for x in issues)


def test_crop_occurrence_limit_is_propagated_to_spatial_limitations():
    record = FAIRagroApplicationDataFitnessModel(
        dataset_characteristics=DatasetCharacteristics(
            dataset_name="x", description="x", geographic_scope="Germany",
            spatial=DatasetSpatialCharacteristics(known_scale_limitations=["1 km does not resolve fields."])
        ),
        limitations_and_risks=LimitationsAndRisks(
            known_limitations=["The product is not masked by actual crop occurrence or agricultural land use."]
        ),
    )
    out = postprocess_semantics(record)
    text = " ".join(out.dataset_characteristics.spatial.known_scale_limitations).lower()
    assert "crop-occurrence" in text or "crop occurrence" in text
