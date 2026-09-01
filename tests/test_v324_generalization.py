import json
from pathlib import Path

from evidence_postprocess import normalize_evidence_locators
from models import (
    AnalysisCharacteristics,
    AnalysisType,
    DatasetCharacteristics,
    DatasetSpatialCharacteristics,
    EvaluationSupport,
    EvidenceRecord,
    EvidenceType,
    FAIRagroApplicationDataFitnessModel,
    LimitationsAndRisks,
    MetricContext,
    MetricScope,
    ProcessingPipeline,
    RepresentationMethod,
    SourceModality,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)
from semantic_checks import validate_record
from semantic_postprocess import postprocess_semantics
from source_bundle import SourceBundle, _build_source_item_registry, _prose_metric_candidates, _table_metric_candidates


def bundle(*, registry=None, hints=None):
    return SourceBundle(
        package_dir="/tmp/package", source_filename="paper.pdf", source_sha256="abc",
        source_fidelity_status="pass", source_fidelity_initial_status="pass",
        source_fidelity_summary={}, unresolved_items=[], clean_markdown_path="/tmp/x.md",
        text_for_llm="source", bundle_sha256="def", item_counts={},
        source_item_registry=registry or {}, metric_candidate_hints=hints or [],
    )


def evidence(text="metric = 1.0", *, section="3 Validation", item=None, modality=SourceModality.authored_prose):
    return [EvidenceRecord(
        claim="metric reported", evidence_type=EvidenceType.explicit,
        source_text=text, source_section=section, source_item_id=item,
        source_modality=modality,
        representation_method=(RepresentationMethod.docling_structured if item else RepresentationMethod.docling_clean_markdown),
    )]


def test_heading_cleanup_removes_pdf_line_number_artifact():
    md = "## 2 Methods 130\n\nThe workflow is shown in Figure 3.\n"
    fidelity = {"figures": [{"figure_id": "fig_003", "figure_number": "3", "page_no": 7}]}
    reg = _build_source_item_registry(fidelity, md)
    assert reg["fig_003"]["section_hint"] == "2 Methods"


def test_prose_does_not_inherit_nearby_formula_locator():
    rec = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="accuracy", value_or_summary="0.9", context=MetricContext.out_of_sample_validation,
                scope=MetricScope.dataset, scope_label="test dataset",
                evaluation_support=EvaluationSupport.held_out_station,
                evidence=[EvidenceRecord(
                    claim="split", evidence_type=EvidenceType.explicit,
                    source_text="Observations were split 75/25.", source_section="2.4 Validation", source_page=12,
                    source_item_id="formula_007", source_modality=SourceModality.authored_prose,
                    representation_method=RepresentationMethod.docling_clean_markdown,
                )],
            )
        ])
    )
    reg = {"formula_007": {"item_type": "formula", "source_page": 12, "source_location": "Eq. 7", "section_hint": "2.3 Model"}}
    out = normalize_evidence_locators(rec, reg)
    ev = out.validation_and_diagnostics.validation_metrics[0].evidence[0]
    assert ev.source_item_id is None
    assert ev.source_section == "2.4 Validation"
    assert ev.source_location is None


def test_generic_table_metric_candidates_support_non_phase_metrics(tmp_path: Path):
    pkg = tmp_path
    (pkg / "tables" / "tbl_001").mkdir(parents=True)
    (pkg / "tables" / "tbl_001" / "table.records.json").write_text(json.dumps([
        {"Model": "Method A", "NSE": 0.81, "KGE": 0.77, "Mean bias": -0.4, "N": 120}
    ]), encoding="utf-8")
    fidelity = {"tables": [{"table_id": "tbl_001", "folder": "tables/tbl_001", "page_no": 4, "caption": "Validation metrics"}]}
    reg = {"tbl_001": {"item_type": "table", "source_page": 4, "source_location": "Table 1", "section_hint": "3 Validation"}}
    hints = _table_metric_candidates(pkg, fidelity, reg)
    keys = {h["metric_key"] for h in hints}
    assert {"nse", "kge", "bias"}.issubset(keys)
    assert "n" not in keys
    assert {h["target"] for h in hints} == {"Method A"}


def test_generic_selected_table_target_requires_generic_sibling():
    rec = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="NSE", value_or_summary="0.81", context=MetricContext.out_of_sample_validation,
                scope=MetricScope.model_or_method, scope_label="Method A",
                evidence=evidence("Method A NSE 0.81", item="tbl_001", modality=SourceModality.author_table),
            )
        ])
    )
    hints = [
        {"kind": "structured_table_metric", "metric_name": "NSE", "metric_key": "nse", "target": "Method A", "value": "0.81", "source_item_id": "tbl_001"},
        {"kind": "structured_table_metric", "metric_name": "KGE", "metric_key": "kge", "target": "Method A", "value": "0.77", "source_item_id": "tbl_001"},
    ]
    issues = validate_record(rec, bundle(hints=hints))
    assert any(x.code == "STRUCTURED_TABLE_METRIC_SIBLINGS_MISSING" for x in issues)


def test_generic_downstream_custom_index_candidate_is_detected():
    md = """## 4 Case study\n\nFor Site Alpha, QSI = 0.84 ± 0.03 in the demonstrated application.\n"""
    hints = _prose_metric_candidates(md)
    assert len(hints) == 1
    assert hints[0]["downstream_use_case_result"] is True
    assert "qsi" in hints[0]["metric_keys"]
    assert hints[0]["target_hint"].lower() == "site alpha"


def test_unsupported_model_calibration_is_removed_but_true_calibration_is_retained():
    rec = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.regression, AnalysisType.model_calibration],
            modeling_approach=["Random forest regression"],
        ),
        processing_pipeline=ProcessingPipeline(model_fitting=["Fit model to training data"]),
    )
    out = postprocess_semantics(rec)
    assert AnalysisType.model_calibration not in (out.analysis_characteristics.analysis_type or [])

    rec2 = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.model_calibration],
            modeling_approach=["Calibrated model parameters against observed data"],
        )
    )
    out2 = postprocess_semantics(rec2)
    assert AnalysisType.model_calibration in (out2.analysis_characteristics.analysis_type or [])


def test_support_like_scope_with_named_target_is_error():
    rec = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="uncertainty", value_or_summary="1.2", context=MetricContext.spatial_uncertainty_summary,
                scope=MetricScope.spatial_surface_summary, scope_label="Layer A",
                evaluation_support=EvaluationSupport.spatial_surface,
                evidence=evidence("Layer A uncertainty = 1.2"),
            )
        ])
    )
    issues = validate_record(rec, bundle())
    assert any(x.code == "METRIC_SCOPE_MIXES_TARGET_AND_SUPPORT" and x.severity == "error" for x in issues)


def test_spatial_limitation_propagation_reuses_source_derived_sentence_without_document_hardcoding():
    sentence = "Predictions outside the sampled elevation range require caution."
    rec = FAIRagroApplicationDataFitnessModel(
        dataset_characteristics=DatasetCharacteristics(
            dataset_name="Dataset X", description="x", geographic_scope="Study region",
            spatial=DatasetSpatialCharacteristics(known_scale_limitations=[]),
        ),
        limitations_and_risks=LimitationsAndRisks(extrapolation_limits=[sentence]),
    )
    out = postprocess_semantics(rec)
    assert sentence in out.dataset_characteristics.spatial.known_scale_limitations
    assert not any("Germany" in x for x in out.dataset_characteristics.spatial.known_scale_limitations)


def test_summary_quality_prose_preserves_sibling_metrics_for_same_target():
    md = """## 3 Validation and performance\n\nFor Site Alpha, mean NSE = 0.81 and KGE = 0.77 across years.\n"""
    hints = _prose_metric_candidates(md)
    assert hints and hints[0]["quality_context"] and hints[0]["summary_signal"]
    rec = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="NSE", value_or_summary="0.81", context=MetricContext.out_of_sample_validation,
                scope=MetricScope.study_or_site, scope_label="Site Alpha",
                evidence=evidence("For Site Alpha, mean NSE = 0.81 and KGE = 0.77 across years.", section="3 Validation and performance"),
            )
        ])
    )
    issues = validate_record(rec, bundle(hints=hints))
    assert any(x.code == "SUMMARY_PROSE_METRIC_UNREPRESENTED" for x in issues)


def test_downstream_candidate_requires_section_anchored_downstream_metric():
    md = """## 4 Demonstrated application\n\nFor Region B, QSI = 0.84 ± 0.03.\n"""
    hints = _prose_metric_candidates(md)
    rec = FAIRagroApplicationDataFitnessModel(validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[]))
    issues = validate_record(rec, bundle(hints=hints))
    assert any(x.code == "SOURCE_DOWNSTREAM_NUMERIC_RESULT_UNREPRESENTED" for x in issues)


def test_heading_like_quality_statement_is_detected_without_crop_hardcoding():
    md = """## 3.4 Uncertainty assessment\n\nFor the flowering stage, mean PICP was 0.91 (range 0.88-0.93) and MPIW was 14.2 days.\n"""
    hints = _prose_metric_candidates(md)
    assert hints
    assert {"picp", "mpiw"}.issubset(set(hints[0]["metric_keys"]))
    assert hints[0]["target_hint"].lower() == "flowering stage"


def test_subject_verb_target_hint_detects_summary_without_for_prefix():
    md = """## 3 Validation accuracy\n\nMethod A achieved mean RMSE = 2.4 across folds.\n"""
    hints = _prose_metric_candidates(md)
    assert hints and hints[0]["target_hint"].lower() == "method a"
