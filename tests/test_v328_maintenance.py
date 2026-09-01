from __future__ import annotations

from models import (
    AnalysisCharacteristics,
    AnalysisType,
    EvidenceRecord,
    EvidenceType,
    FAIRagroApplicationDataFitnessModel,
    RepresentationMethod,
    SourceModality,
)
from semantic_checks import validate_record
from semantic_postprocess import postprocess_semantics
from source_bundle import SourceBundle


def _bundle() -> SourceBundle:
    return SourceBundle(
        package_dir=".",
        source_filename="paper.pdf",
        source_sha256="x",
        source_fidelity_status="pass",
        source_fidelity_initial_status="pass",
        source_fidelity_summary={},
        unresolved_items=[],
        clean_markdown_path="paper.md",
        text_for_llm="",
        bundle_sha256="b",
        item_counts={},
        source_item_registry={},
        metric_candidate_hints=[],
        metric_candidate_focus=[],
    )


def test_structured_visual_recovery_accepts_authored_caption_modality():
    record = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            evidence=[
                EvidenceRecord(
                    claim="A figure caption reports a quantitative result.",
                    evidence_type=EvidenceType.explicit,
                    source_section="3 Results",
                    source_location="Figure 2",
                    source_text="Figure 2. The reported value is 4.2.",
                    source_item_id="fig_002",
                    source_modality=SourceModality.author_caption,
                    representation_method=RepresentationMethod.structured_visual_recovery,
                )
            ]
        )
    )
    issues = validate_record(record, _bundle())
    assert all(i.code != "VISUAL_RECOVERY_MODALITY_MISMATCH" for i in issues)


def test_machine_learning_label_removed_for_statistical_interpolation_only():
    record = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.regression, AnalysisType.interpolation, AnalysisType.machine_learning],
            modeling_approach=[
                "Generalized additive regression model with smooth spatial terms.",
                "Universal kriging and thin-plate spline interpolation.",
            ],
        )
    )
    out = postprocess_semantics(record)
    assert AnalysisType.machine_learning not in (out.analysis_characteristics.analysis_type or [])
    assert AnalysisType.regression in (out.analysis_characteristics.analysis_type or [])
    assert AnalysisType.interpolation in (out.analysis_characteristics.analysis_type or [])


def test_machine_learning_label_retained_for_explicit_ml_workflow():
    record = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.machine_learning],
            modeling_approach=["Random forest regression with cross-validated hyperparameters."],
        )
    )
    out = postprocess_semantics(record)
    assert AnalysisType.machine_learning in (out.analysis_characteristics.analysis_type or [])


def test_validator_flags_unprocessed_machine_learning_without_workflow_support():
    record = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.machine_learning],
            modeling_approach=["Generalized additive regression model with spatial smooths."],
        )
    )
    issues = validate_record(record, _bundle())
    assert any(i.code == "MACHINE_LEARNING_ANALYSIS_WITHOUT_WORKFLOW_SUPPORT" and i.severity == "error" for i in issues)
