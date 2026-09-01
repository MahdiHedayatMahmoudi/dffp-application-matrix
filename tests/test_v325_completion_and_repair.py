import json
import sys
import types
from pathlib import Path

if "openai" not in sys.modules:
    _fake_openai = types.ModuleType("openai")
    _fake_openai.OpenAI = object
    sys.modules["openai"] = _fake_openai

from llm_service import LLMRunMetadata, StructuredExtraction
from models import (
    AnalysisCharacteristics,
    AnalysisType,
    EvaluationSupport,
    EvidenceRecord,
    EvidenceType,
    FAIRagroApplicationDataFitnessModel,
    MetricContext,
    MetricScope,
    ProcessingPipeline,
    ProducerWorkflowInputs,
    RepresentationMethod,
    SourceModality,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)
from pipeline import FullDFFPPipeline
from semantic_checks import validate_record
from semantic_postprocess import postprocess_semantics
from settings import Settings
from source_bundle import SourceBundle, _metric_candidate_focus, _prose_metric_candidates


def _bundle(hints):
    return SourceBundle(
        package_dir="/tmp/pkg", source_filename="paper.pdf", source_sha256="abc",
        source_fidelity_status="pass", source_fidelity_initial_status="pass",
        source_fidelity_summary={}, unresolved_items=[], clean_markdown_path="/tmp/paper.md",
        text_for_llm="source", bundle_sha256="def", item_counts={},
        source_item_registry={}, metric_candidate_hints=hints,
        metric_candidate_focus=_metric_candidate_focus(hints),
    )


def _ev(text, section="3 Validation", item=None, page=None, location=None):
    return [EvidenceRecord(
        claim="reported metric", evidence_type=EvidenceType.explicit,
        source_text=text, source_section=section, source_item_id=item,
        source_page=page, source_location=location,
        source_modality=SourceModality.author_table if item else SourceModality.authored_prose,
        representation_method=RepresentationMethod.docling_structured if item else RepresentationMethod.docling_clean_markdown,
    )]


def test_sentence_windows_capture_multiple_summary_metrics_at_specific_target():
    md = """## 3.4 Uncertainty and performance\n\nPrediction-interval width summarizes sharpness. For Site Alpha, mean PICP was 0.91 and mean interval width was 14.2 days across years.\n"""
    hints = _prose_metric_candidates(md)
    assert any({"picp", "mpiw"}.issubset(set(h["metric_keys"])) for h in hints)
    assert any((h.get("target_hint") or "").lower() == "site alpha" for h in hints)


def test_high_priority_focus_prioritizes_downstream_and_summary_prose():
    md = """## 3 Validation\n\nFor Method A, mean RMSE = 2.4 across folds.\n\n## 4 Demonstrated application\n\nFor Site B, QSI = 0.84 ± 0.03 mm.\n"""
    hints = _prose_metric_candidates(md)
    focus = _metric_candidate_focus(hints)
    assert any(x.get("downstream_use_case_result") for x in focus)
    assert any(x.get("source_section") == "3 Validation" for x in focus)


def test_target_metric_completeness_matches_across_section_hierarchy():
    md = """## 3.3.4 Uncertainty assessment\n\nFor the flowering stage, mean PICP was 0.91 and mean interval width was 14.2 days across years.\n"""
    hints = _prose_metric_candidates(md)
    rec = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="PICP", value_or_summary="0.91", context=MetricContext.cross_validated_interval_assessment,
                scope=MetricScope.subgroup, scope_label="Flowering stage",
                evaluation_support=EvaluationSupport.held_out_station,
                evidence=_ev("PICP 0.91", section="3.3 Example results"),
            )
        ])
    )
    issues = validate_record(rec, _bundle(hints))
    assert any(x.code == "SUMMARY_PROSE_METRIC_UNREPRESENTED" and "mpiw" in x.message.lower() for x in issues)


def test_remote_sensing_application_leakage_is_removed_but_true_remote_sensing_is_kept():
    rec = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.regression, AnalysisType.remote_sensing],
            modeling_approach=["Generalized additive regression"],
        ),
        processing_pipeline=ProcessingPipeline(model_fitting=["Fit a spatial regression model"]),
    )
    out = postprocess_semantics(rec)
    assert AnalysisType.remote_sensing not in (out.analysis_characteristics.analysis_type or [])

    rec2 = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.remote_sensing],
            modeling_approach=["Classify Sentinel-2 multispectral imagery"],
        ),
        producer_workflow_inputs=ProducerWorkflowInputs(data_types=["Sentinel-2 satellite imagery"]),
    )
    out2 = postprocess_semantics(rec2)
    assert AnalysisType.remote_sensing in (out2.analysis_characteristics.analysis_type or [])


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="x", extraction_model="test", extraction_reasoning_effort=None,
        extraction_max_output_tokens=1000, timeout_seconds=10, max_retries=0,
        system_prompt_version="system_v2", extraction_prompt_version="extraction_v9", schema_version="test",
        source_package_root=str(tmp_path), ingestion_table_mode="accurate", ingestion_formula_enrichment=True,
        ingestion_ocr_mode="auto", ingestion_render_dpi=300, recovery_enabled_by_default=False,
        recovery_api_url="https://example.test/responses", recovery_api_type="responses", recovery_model="test",
        recovery_reasoning_effort=None, recovery_max_tokens=1000, recovery_priorities=("high", "medium"),
        recovery_statuses=("required", "recommended", "review"), include_all_structured_tables=True,
        include_recovered_figures=True, include_formulas=True, max_structured_item_chars=10000,
        semantic_repair_enabled=True, semantic_repair_max_attempts=1, semantic_repair_prompt_version="repair_v1",
    )


def _meta(prompt="extraction_v9"):
    return LLMRunMetadata(
        generated_at_utc="2026-01-01T00:00:00+00:00", api="test", requested_model="test",
        response_model="test", response_id="r", system_prompt_version="system_v2",
        extraction_prompt_version=prompt, system_prompt_sha256="s", extraction_prompt_sha256="e",
        schema_version="test", source_bundle_sha256="b", duration_seconds=0.1,
    )


class _RepairingExtractor:
    def __init__(self):
        self.repair_calls = 0

    def extract(self, bundle):
        rec = FAIRagroApplicationDataFitnessModel(
            validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
                ValidationMetricRecord(
                    name="NSE", value_or_summary="0.81", context=MetricContext.out_of_sample_validation,
                    scope=MetricScope.model_or_method, scope_label="Method A",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=_ev("Method A NSE 0.81", item="tbl_001", page=4, location="Table 1"),
                )
            ])
        )
        return StructuredExtraction(rec, _meta())

    def repair(self, bundle, record, issues):
        self.repair_calls += 1
        fixed = record.model_copy(deep=True)
        fixed.validation_and_diagnostics.validation_metrics.append(
            ValidationMetricRecord(
                name="KGE", value_or_summary="0.77", context=MetricContext.out_of_sample_validation,
                scope=MetricScope.model_or_method, scope_label="Method A",
                evaluation_support=EvaluationSupport.held_out_station,
                evidence=_ev("Method A KGE 0.77", item="tbl_001", page=4, location="Table 1"),
            )
        )
        return StructuredExtraction(fixed, _meta("repair_v1"))


def test_pipeline_uses_one_semantic_repair_when_initial_record_has_source_completeness_error(tmp_path: Path):
    pkg = tmp_path / "pkg"
    (pkg / "docling").mkdir(parents=True)
    (pkg / "audit").mkdir()
    (pkg / "tables" / "tbl_001").mkdir(parents=True)
    (pkg / "docling" / "document.clean.md").write_text("# Paper\n\n## 3 Validation\n\nResults are in Table 1.\n", encoding="utf-8")
    (pkg / "tables" / "tbl_001" / "table.records.json").write_text(json.dumps([
        {"Model": "Method A", "NSE": 0.81, "KGE": 0.77}
    ]), encoding="utf-8")
    (pkg / "source_package_manifest.json").write_text(json.dumps({"core_exports": {"clean_markdown": "docling/document.clean.md"}}), encoding="utf-8")
    (pkg / "audit" / "source_fidelity.json").write_text(json.dumps({
        "document": {"filename": "paper.pdf", "sha256": "abc"},
        "summary": {"overall_status": "pass"}, "recovery_queue": [],
        "tables": [{"table_id": "tbl_001", "table_number": "1", "folder": "tables/tbl_001", "page_no": 4, "caption": "Validation metrics"}],
        "formulas": [], "figures": [],
    }), encoding="utf-8")

    extractor = _RepairingExtractor()
    result = FullDFFPPipeline(_settings(tmp_path), extractor=extractor).extract_package(pkg)
    assert extractor.repair_calls == 1
    assert not [x for x in result.validation_issues if x.severity == "error"]
    names = {m.name for m in result.record.validation_and_diagnostics.validation_metrics}
    assert {"NSE", "KGE"}.issubset(names)
    assert len(result.manifest["semantic_repair_runs"]) == 1

def test_passive_subject_target_hint_detects_fine_grained_result():
    md = """## 3.3.2 Accuracy across groups\n\nThe flowering stage (B) is predicted most accurately, with an RMSE of 2.8-5.1 units (median 3.9).\n"""
    hints = _prose_metric_candidates(md)
    assert hints
    assert hints[0]["target_hint"].lower() == "flowering stage"
    assert "rmse" in hints[0]["metric_keys"]

def test_short_metric_aliases_do_not_match_inside_unrelated_words():
    md = """## 3 Uncertainty assessment\n\nThe observed coverage is close to nominal, with mean PICP of 0.90 and mean interval width of 12.0 days.\n"""
    hints = _prose_metric_candidates(md)
    keys = set().union(*(set(h["metric_keys"]) for h in hints))
    assert "picp" in keys and "mpiw" in keys
    assert "bse" not in keys
    assert "p_value" not in keys
    assert "standard_error" not in keys

def test_structured_metric_candidate_order_follows_authored_column_order(tmp_path: Path):
    from source_bundle import _table_metric_candidates
    pkg = tmp_path
    (pkg / "tables" / "tbl_001").mkdir(parents=True)
    (pkg / "tables" / "tbl_001" / "table.records.json").write_text(
        json.dumps([{"Model": "A", "NSE": 0.8, "KGE": 0.7, "Mean bias": -0.2}]), encoding="utf-8"
    )
    fidelity = {"tables": [{"table_id": "tbl_001", "folder": "tables/tbl_001", "page_no": 1}]}
    hints = _table_metric_candidates(pkg, fidelity, {"tbl_001": {}})
    assert [h["metric_key"] for h in hints] == ["nse", "kge", "bias"]
