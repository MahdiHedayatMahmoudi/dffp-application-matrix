from models import (
    FAIRagroApplicationDataFitnessModel,
    FitnessClassification,
    FitnessAssessmentBasis,
)
from semantic_checks import validate_record
from source_bundle import SourceBundle


def dummy_bundle():
    return SourceBundle(
        package_dir="/tmp/package",
        source_filename="paper.pdf",
        source_sha256="abc",
        source_fidelity_status="pass",
        source_fidelity_initial_status="pass",
        source_fidelity_summary={},
        unresolved_items=[],
        clean_markdown_path="/tmp/package/docling/document.clean.md",
        text_for_llm="source",
        bundle_sha256="def",
        item_counts={"tables": 0, "formulas": 0, "recovered_figures": 0},
    )


def metric_evidence(text: str = "Metric value 1.0"):
    from models import EvidenceRecord, EvidenceType
    return [EvidenceRecord(claim="Metric is reported.", evidence_type=EvidenceType.explicit, source_text=text)]


def test_unassessed_fitness_with_lists_is_flagged():
    record = FAIRagroApplicationDataFitnessModel(
        fitness_classification=FitnessClassification(
            suitable_for=["something"],
            assessment_basis=FitnessAssessmentBasis.unassessed,
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "UNASSESSED_FITNESS_HAS_CLASSIFICATION" for x in issues)


def test_downstream_producer_overlap_is_flagged():
    from models import InputDataRequirements, ProducerWorkflowInputs

    record = FAIRagroApplicationDataFitnessModel(
        input_data_requirements=InputDataRequirements(
            data_types=[
                "Gridded daily mean temperature data",
                "Digital elevation model",
                "E-OBS precipitation ensemble data",
            ],
            required_sources=["1 km DEM"],
        ),
        producer_workflow_inputs=ProducerWorkflowInputs(
            data_types=[
                "Gridded daily mean temperature data",
                "Digital elevation model",
            ],
            sources=["BKG 1 km DEM"],
        ),
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "DOWNSTREAM_PRODUCER_INPUT_OVERLAP" for x in issues)


def test_distinct_downstream_and_producer_inputs_are_not_flagged():
    from models import InputDataRequirements, ProducerWorkflowInputs

    record = FAIRagroApplicationDataFitnessModel(
        input_data_requirements=InputDataRequirements(
            data_types=[
                "Published phase-entry DOY surfaces",
                "Matching BSE layers",
                "Daily precipitation and ensemble spread",
            ],
            required_sources=["E-OBS v33.0e"],
        ),
        producer_workflow_inputs=ProducerWorkflowInputs(
            data_types=[
                "DWD phenological station observations",
                "Gridded daily mean temperature data",
                "Digital elevation model",
            ],
        ),
    )
    issues = validate_record(record, dummy_bundle())
    assert not any(x.code == "DOWNSTREAM_PRODUCER_INPUT_OVERLAP" for x in issues)


def test_deprecated_prediction_interval_calibration_wording_is_flagged():
    from models import DecisionRiskProfile

    record = FAIRagroApplicationDataFitnessModel(
        decision_risk_profile=DecisionRiskProfile(
            critical_quality_dimensions=["Prediction-interval calibration"]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "DEPRECATED_INTERVAL_CALIBRATION_TERMINOLOGY" for x in issues)


def test_authored_source_text_may_preserve_legacy_calibration_wording():
    from models import DatasetCharacteristics, EvidenceRecord, EvidenceType

    record = FAIRagroApplicationDataFitnessModel(
        dataset_characteristics=DatasetCharacteristics(
            evidence=[
                EvidenceRecord(
                    claim="Prediction intervals are evaluated separately.",
                    evidence_type=EvidenceType.explicit,
                    source_text="Prediction-interval calibration is assessed separately.",
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert not any(x.code == "DEPRECATED_INTERVAL_CALIBRATION_TERMINOLOGY" for x in issues)


def test_picp_without_evaluation_support_is_flagged():
    from models import MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="PICP",
                    value_or_summary="0.90",
                    context=MetricContext.cross_validated_interval_assessment,
                    scope=MetricScope.dataset_family,
                    evidence=metric_evidence("PICP 0.90"),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "INTERVAL_METRIC_EVALUATION_SUPPORT_UNKNOWN" for x in issues)


def test_picp_with_held_out_station_support_is_not_flagged():
    from models import EvaluationSupport, MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="PICP",
                    value_or_summary="0.90",
                    context=MetricContext.cross_validated_interval_assessment,
                    scope=MetricScope.dataset_family,
                    evaluation_support=EvaluationSupport.held_out_station,
                    evaluation_population="Held-out phenological observation stations",
                    evidence=metric_evidence("PICP 0.90"),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert not any(x.code == "INTERVAL_METRIC_EVALUATION_SUPPORT_UNKNOWN" for x in issues)


def test_metric_scope_label_must_not_mix_targets():
    from models import EvaluationSupport, MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="MAE",
                    value_or_summary="winter wheat 5.7; all crops 5.2",
                    context=MetricContext.out_of_sample_validation,
                    scope=MetricScope.crop,
                    scope_label="winter wheat; all crops",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=metric_evidence(),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "METRIC_SCOPE_LABEL_MULTIPLE_TARGETS" for x in issues)
    assert any(x.code == "METRIC_SCOPE_LABEL_CONTRADICTS_SCOPE" for x in issues)


def test_crop_phase_scope_is_valid_without_specific_year():
    from models import EvaluationSupport, MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="PICP",
                    value_or_summary="mean 0.900 across years",
                    context=MetricContext.cross_validated_interval_assessment,
                    scope=MetricScope.crop_phase,
                    scope_label="winter wheat heading",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=metric_evidence(),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert not any(x.code == "PHASE_YEAR_SCOPE_WITHOUT_YEAR" for x in issues)
    assert not any(x.code == "METRIC_SCOPE_LABEL_MISSING" for x in issues)


def test_phase_year_scope_without_year_is_flagged():
    from models import EvaluationSupport, MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="PICP",
                    value_or_summary="mean 0.900 across years",
                    context=MetricContext.cross_validated_interval_assessment,
                    scope=MetricScope.phase_year,
                    scope_label="winter wheat heading",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=metric_evidence(),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "PHASE_YEAR_SCOPE_WITHOUT_YEAR" for x in issues)


def test_inferred_beneficiaries_in_plain_field_are_flagged():
    from models import ApplicationProfile, ExtractionProvenance

    record = FAIRagroApplicationDataFitnessModel(
        application_profile=ApplicationProfile(beneficiaries=["Agro-environmental researchers"]),
        extraction_provenance=ExtractionProvenance(
            inferred=["Beneficiary groups were conservatively inferred from application domains."]
        ),
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "BENEFICIARIES_POPULATED_BUT_PROVENANCE_SAYS_INFERRED" for x in issues)


def test_dataset_family_scope_with_specific_crop_is_flagged():
    from models import EvaluationSupport, MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="MAE",
                    value_or_summary="5.7",
                    context=MetricContext.out_of_sample_validation,
                    scope=MetricScope.dataset_family,
                    scope_label="winter wheat",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=metric_evidence("Winter wheat MAE 5.7 days"),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "DATASET_FAMILY_SCOPE_WITH_SPECIFIC_TARGET" for x in issues)


def test_dataset_family_scope_with_all_crops_is_valid():
    from models import EvaluationSupport, MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="MAE",
                    value_or_summary="5.2",
                    context=MetricContext.out_of_sample_validation,
                    scope=MetricScope.dataset_family,
                    scope_label="all crops",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=metric_evidence("All crops MAE 5.2 days"),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert not any(x.code == "DATASET_FAMILY_SCOPE_WITH_SPECIFIC_TARGET" for x in issues)


def test_metric_evidence_with_numeric_sibling_warns_if_sibling_record_missing():
    from models import EvaluationSupport, MetricContext, MetricScope, ValidationAndDiagnostics, ValidationMetricRecord

    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(
            validation_metrics=[
                ValidationMetricRecord(
                    name="MAE",
                    value_or_summary="5.2",
                    context=MetricContext.out_of_sample_validation,
                    scope=MetricScope.dataset_family,
                    scope_label="all crops",
                    evaluation_support=EvaluationSupport.held_out_station,
                    evidence=metric_evidence("All crops MAE 5.2 days and RMSE 6.5 days."),
                )
            ]
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "METRIC_EVIDENCE_SUGGESTS_MISSING_SIBLING" for x in issues)


def test_model_calibration_without_workflow_calibration_is_flagged():
    from models import AnalysisCharacteristics, AnalysisType

    record = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.model_calibration],
            modeling_approach=["Spatial interpolation using BAM"],
        )
    )
    issues = validate_record(record, dummy_bundle())
    assert any(x.code == "MODEL_CALIBRATION_ANALYSIS_WITHOUT_WORKFLOW_SUPPORT" for x in issues)


def test_dataset_family_all_eight_crops_is_valid_scope_label():
    from models import ValidationAndDiagnostics, ValidationMetricRecord, MetricContext, MetricScope
    record = FAIRagroApplicationDataFitnessModel(
        validation_and_diagnostics=ValidationAndDiagnostics(validation_metrics=[
            ValidationMetricRecord(
                name="MAE", value_or_summary="5.2", unit="days",
                context=MetricContext.out_of_sample_validation,
                scope=MetricScope.dataset_family, scope_label="all eight crops",
                evidence=metric_evidence("All crops MAE 5.2"),
            )
        ])
    )
    issues = validate_record(record, dummy_bundle())
    assert not any(x.code == "DATASET_FAMILY_SCOPE_WITH_SPECIFIC_TARGET" for x in issues)


def test_model_calibration_without_workflow_support_is_error():
    from models import AnalysisCharacteristics, AnalysisType
    record = FAIRagroApplicationDataFitnessModel(
        analysis_characteristics=AnalysisCharacteristics(
            analysis_type=[AnalysisType.model_calibration],
            modeling_approach=["Spatial interpolation with BAM"],
        )
    )
    issues = validate_record(record, dummy_bundle())
    hit = [x for x in issues if x.code == "MODEL_CALIBRATION_ANALYSIS_WITHOUT_WORKFLOW_SUPPORT"]
    assert hit and hit[0].severity == "error"
