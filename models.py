# models_extraction_v2.py
# Publication-oriented FAIRagro / DFFP extraction schema
# Pydantic v2

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)


# -----------------------------------------------------------------------------
# Base model
# -----------------------------------------------------------------------------

class StrictModel(BaseModel):
    """Base model used for structured LLM extraction.

    `extra='forbid'` keeps the generated JSON stable and prevents the model from
    inventing undeclared top-level keys. `populate_by_name=True` allows fields
    such as `global_` to serialize with the alias `global`.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=False,
    )


# Pydantic's default JSON Schema for Decimal accepts either a JSON number or a
# numeric string.  The numeric-string branch uses a regex negative lookahead,
# which is not supported by OpenAI Structured Outputs.  These annotations keep
# Decimal as the in-memory validation type while exposing only JSON numbers to
# the API schema.  ``as_reported`` separately preserves source precision and
# formatting exactly as printed in the paper.
JsonNumberDecimal = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
NonNegativeJsonNumberDecimal = Annotated[
    Decimal,
    Field(ge=0),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "minimum": 0}),
]
UnitIntervalJsonNumberDecimal = Annotated[
    Decimal,
    Field(ge=0, le=1),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "minimum": 0, "maximum": 1}),
]


# -----------------------------------------------------------------------------
# Evidence / provenance primitives
# -----------------------------------------------------------------------------

class EvidenceType(str, Enum):
    explicit = "explicit"
    inferred = "inferred"
    derived = "derived"
    assumed = "assumed"
    unknown = "unknown"


class ReviewStatus(str, Enum):
    not_reviewed = "not_reviewed"
    machine_extracted = "machine_extracted"
    human_verified = "human_verified"
    human_corrected = "human_corrected"


class SourceModality(str, Enum):
    authored_prose = "authored_prose"
    author_table = "author_table"
    author_figure = "author_figure"
    author_formula = "author_formula"
    author_caption = "author_caption"
    repository_metadata = "repository_metadata"
    unknown = "unknown"


class RepresentationMethod(str, Enum):
    docling_clean_markdown = "docling_clean_markdown"
    docling_structured = "docling_structured"
    native_pdf_text = "native_pdf_text"
    structured_visual_recovery = "structured_visual_recovery"
    deterministic_metadata = "deterministic_metadata"
    unknown = "unknown"


class EvidenceRecord(StrictModel):
    claim: str = Field(
        ...,
        min_length=1,
        description="The normalized machine-readable claim supported by the source document.",
    )
    evidence_type: EvidenceType = Field(
        ...,
        description=(
            "Use 'explicit' only when the source directly states the claim; "
            "'inferred' for a conservative interpretation; 'derived' only when a "
            "declared deterministic rule/calculation was applied; 'assumed' for an "
            "unstated assumption; otherwise 'unknown'."
        ),
    )
    source_section: Optional[str] = Field(
        None,
        description="Section/heading in the source, if reliably identifiable; otherwise null.",
    )
    source_location: Optional[str] = Field(
        None,
        description=(
            "Human-readable source locator such as 'Sect. 4.4, limitation 2'. "
            "Do not invent page numbers when the input text does not preserve them."
        ),
    )
    source_text: Optional[str] = Field(
        None,
        description="Short supporting source fragment or close paraphrase. Keep concise.",
    )
    source_page: Optional[int] = Field(
        None,
        ge=1,
        description="1-based PDF page number only when the source bundle provides it reliably.",
    )
    source_item_id: Optional[str] = Field(
        None,
        description="Stable source-package item ID such as tbl_003, formula_005, or fig_004 when applicable.",
    )
    source_modality: SourceModality = Field(
        SourceModality.unknown,
        description="Authored scientific modality supporting the claim.",
    )
    representation_method: RepresentationMethod = Field(
        RepresentationMethod.unknown,
        description=(
            "How the authored source was represented for extraction. This is separate from evidence_type: "
            "an explicit claim can be transcribed through structured_visual_recovery."
        ),
    )
    source_asset_sha256: Optional[str] = Field(
        None,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="SHA-256 of the table/figure/formula recovery asset when supplied by the source bundle.",
    )
    representation_confidence: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Confidence of a derived transcription/representation (expected 0-1), not scientific quality of the source.",
    )
    confidence_note: Optional[str] = Field(
        None,
        description="Brief note about ambiguity or extraction uncertainty; null if unnecessary.",
    )
    review_status: ReviewStatus = Field(
        ReviewStatus.machine_extracted,
        description="Human-review state of this extracted claim.",
    )


# -----------------------------------------------------------------------------
# Document metadata and related resources
# -----------------------------------------------------------------------------

class RelatedResourceType(str, Enum):
    dataset = "dataset"
    collection = "collection"
    software = "software"
    workflow = "workflow"
    input_data = "input_data"
    repository = "repository"
    vocabulary = "vocabulary"
    other = "other"


class IdentifierObjectType(str, Enum):
    dataset = "dataset"
    collection = "collection"
    software = "software"
    workflow = "workflow"
    input_data = "input_data"
    repository = "repository"
    vocabulary = "vocabulary"
    article = "article"
    unknown = "unknown"


class IdentifierStatus(str, Enum):
    source_explicit = "source_explicit"
    inferred = "inferred"
    verification_required = "verification_required"
    unresolved = "unresolved"


class RelatedResource(StrictModel):
    name: Optional[str] = None
    identifier: Optional[str] = Field(
        None,
        description="DOI, URL, accession, repository identifier, or other persistent identifier.",
    )
    resource_type: RelatedResourceType = RelatedResourceType.other
    relation: Optional[str] = Field(
        None,
        description="Relationship to the source document, e.g. 'describes', 'uses', 'isSupplementedBy'.",
    )
    version: Optional[str] = None
    is_concept_identifier: Optional[bool] = Field(
        None,
        description="True only when the source explicitly identifies the DOI/identifier as a concept identifier.",
    )
    identifier_object_type: IdentifierObjectType = Field(
        IdentifierObjectType.unknown,
        description=(
            "Type of object actually identified by identifier. Keep this separate from resource_type so an "
            "article DOI accidentally attached to a dataset can be detected."
        ),
    )
    identifier_status: IdentifierStatus = Field(
        IdentifierStatus.unresolved,
        description="Whether the source states the identifier directly or it still needs verification.",
    )
    identifier_note: Optional[str] = Field(
        None,
        description="Concise reason for uncertainty or mismatch; do not guess an identifier.",
    )
    evidence: Optional[List[EvidenceRecord]] = None


class DocumentStatus(str, Enum):
    draft = "draft"
    preprint = "preprint"
    published = "published"
    report = "report"
    unknown = "unknown"


class DocumentMetadata(StrictModel):
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    doi: Optional[str] = Field(
        None,
        description=(
            "DOI of the SOURCE DOCUMENT ONLY. Do not place dataset, collection, software, "
            "or workflow DOIs here. If the manuscript has no DOI yet, use null."
        ),
    )
    publication_year: Optional[int] = None
    source: Optional[str] = None
    keywords: Optional[List[str]] = None
    document_type: Optional[str] = Field(
        None,
        description="For example: data paper, application paper, technical report, methods paper.",
    )
    document_status: DocumentStatus = Field(
        DocumentStatus.unknown,
        description="Publication state supported by the source package; do not infer published from formatting alone.",
    )
    generated_subject_terms: Optional[List[str]] = Field(
        None,
        description="Normalized subject terms generated during extraction. Keep source-authored keywords in keywords.",
    )
    related_resources: Optional[List[RelatedResource]] = Field(
        None,
        description="Datasets, collections, software, workflows, vocabularies, and repositories linked to the document.",
    )


# -----------------------------------------------------------------------------
# Dataset characteristics: what the dataset IS / PROVIDES
# -----------------------------------------------------------------------------

class DatasetSpatialCharacteristics(StrictModel):
    native_resolution: Optional[str] = Field(
        None,
        description="Native spatial resolution/support of the published dataset, if stated.",
    )
    spatial_reference: Optional[str] = None
    geographic_extent: Optional[str] = None
    support_or_unit: Optional[str] = Field(
        None,
        description="Spatial support/unit represented by a datum, when relevant.",
    )
    known_scale_limitations: Optional[List[str]] = None


class DatasetTemporalCharacteristics(StrictModel):
    temporal_extent: Optional[str] = None
    temporal_resolution_or_sampling: Optional[str] = None
    update_frequency: Optional[str] = None
    published_dataset_gaps: Optional[List[str]] = Field(
        None, description="Missing periods/surfaces in the published data product itself."
    )
    upstream_observation_gaps: Optional[List[str]] = Field(
        None, description="Gaps in source observations used to construct the dataset; do not conflate with published-data gaps."
    )
    historical_coverage_constraints: Optional[List[str]] = None
    known_temporal_gaps: Optional[List[str]] = Field(
        None, description="Legacy combined field; prefer the more specific gap fields above."
    )


class DatasetCharacteristics(StrictModel):
    dataset_name: Optional[str] = None
    description: Optional[str] = None
    geographic_scope: Optional[str] = None
    spatial: Optional[DatasetSpatialCharacteristics] = None
    temporal: Optional[DatasetTemporalCharacteristics] = None
    variables_or_layers: Optional[List[str]] = None
    formats: Optional[List[str]] = None
    access_and_packaging: Optional[List[str]] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Application profile: how the document says the dataset is / could be used
# -----------------------------------------------------------------------------

class ApplicationProfile(StrictModel):
    application_area: Optional[List[str]] = None
    intended_use: Optional[str] = None
    use_case_problem: Optional[List[str]] = None
    beneficiaries: Optional[List[str]] = Field(
        None,
        description=(
            "Beneficiary/user groups only when explicitly named or directly identified by the source document. "
            "Do not infer beneficiary groups merely from application domains; leave null instead."
        ),
    )
    demonstrated_uses: Optional[List[str]] = Field(
        None,
        description="Uses actually demonstrated in the source document.",
    )
    potential_uses: Optional[List[str]] = Field(
        None,
        description="Uses discussed as possible/potential, but not demonstrated in the source document.",
    )
    recommended_context: Optional[List[str]] = Field(
        None,
        description="Only contexts explicitly recommended or clearly supported by the source.",
    )
    not_suitable_for: Optional[List[str]] = Field(
        None,
        description=(
            "Populate only when the source explicitly states a limitation/incompatibility, "
            "or when a declared deterministic decision rule supports it. Do not invent prohibitions."
        ),
    )
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Analysis characteristics
# -----------------------------------------------------------------------------

class AnalysisType(str, Enum):
    classification = "classification"
    regression = "regression"
    descriptive_statistics = "descriptive statistics"
    comparative_analysis = "comparative analysis"
    hypothesis_testing = "hypothesis testing"
    clustering = "clustering"
    dimensionality_reduction = "dimensionality reduction"
    time_series = "time-series analysis"
    simulation = "simulation"
    optimization = "optimization"
    causal_inference = "causal inference"
    machine_learning = "machine learning"
    remote_sensing = "remote-sensing analysis"
    phenology_indices = "phenology-aware indices"
    uncertainty = "uncertainty estimation"
    interpolation = "spatial interpolation"
    model_calibration = "model calibration"
    prediction_interval_assessment = "prediction-interval assessment"
    other = "other"


class AnalysisCharacteristics(StrictModel):
    analysis_type: Optional[List[AnalysisType]] = None
    modeling_approach: Optional[List[str]] = None
    assumptions: Optional[List[str]] = None
    sensitivity_factors: Optional[List[str]] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Application requirements: what an APPLICATION needs
# (kept separate from dataset characteristics)
# -----------------------------------------------------------------------------

class SpatialRequirement(StrictModel):
    minimum: Optional[str] = Field(
        None,
        description="Minimum spatial requirement of the downstream application, only if explicitly stated.",
    )
    recommended: Optional[str] = Field(
        None,
        description="Recommended spatial requirement of the downstream application, only if explicitly stated.",
    )
    aggregation_levels: Optional[List[str]] = Field(
        None,
        description="Spatial supports/resolutions used or required by the downstream application, including explicit aggregation steps.",
    )


class TemporalRequirement(StrictModel):
    sampling_frequency: Optional[str] = Field(
        None, description="Sampling/frequency required by the downstream application, not producer-only input frequency."
    )
    temporal_extent: Optional[str] = Field(
        None, description="Temporal extent required/used by the downstream application, when explicitly supported."
    )
    latency_or_timeliness: Optional[str] = Field(
        None, description="Downstream latency/timeliness requirement, only when stated or demonstrated."
    )
    phenology_dependency: Optional[bool] = Field(
        None, description="Whether the downstream application explicitly depends on phenology-timed inputs/windows."
    )


class InputDataRequirements(StrictModel):
    """Requirements of a downstream application that uses the published dataset.

    These fields describe what a downstream user needs *after the published data product
    already exists*. Raw observations, covariates, grids, or parameters needed only to
    build/reproduce the dataset belong in ``producer_workflow_inputs`` instead.
    """

    data_types: Optional[List[str]] = Field(
        None,
        description=(
            "Data/product types required by a downstream application using the published dataset. "
            "Include published dataset layers when they are application inputs. Exclude raw observations, "
            "covariates, or intermediate producer inputs used only to build/reproduce the dataset."
        ),
    )
    required_sources: Optional[List[str]] = Field(
        None,
        description=(
            "Named data sources a downstream user must obtain for the described application/use case. "
            "Do not list producer-side source archives merely because they were used to create the published dataset."
        ),
    )
    spatial_resolution: Optional[SpatialRequirement] = Field(
        None,
        description=(
            "Spatial requirements or aggregation steps of the downstream application. Do not copy the dataset's native "
            "resolution here unless the source explicitly makes it an application requirement or the demonstrated use case consumes it at that support."
        ),
    )
    temporal_resolution: Optional[TemporalRequirement] = Field(
        None,
        description=(
            "Temporal requirements of the downstream application/use case, not the temporal inputs used solely by the producer workflow."
        ),
    )
    spectral_or_variable_requirements: Optional[List[str]] = Field(
        None,
        description=(
            "Variables/layers that the downstream application consumes, such as published output layers or additional external variables. "
            "Exclude producer covariates unless the source explicitly says the downstream application also requires them."
        ),
    )
    minimum_sample_size: Optional[str] = Field(
        None,
        description=(
            "Only state a sample-size requirement when the source explicitly associates it with the downstream application. "
            "Method-specific producer sample-size requirements belong in producer_workflow_inputs.method_specific_requirements."
        ),
    )
    dependency_on_multiple_inputs: Optional[bool] = Field(
        None,
        description=(
            "True only when the downstream application itself combines multiple inputs. Do not set true merely because the producer workflow uses multiple inputs."
        ),
    )
    other_requirements: Optional[List[str]] = Field(
        None,
        description=(
            "Other explicit downstream-use requirements. Keep producer processing requirements, model parameters, and reproduction dependencies out of this section."
        ),
    )
    evidence: Optional[List[EvidenceRecord]] = Field(
        None,
        description=(
            "Evidence should point to application/use-case context. Evidence that only documents dataset production is not sufficient to justify a downstream requirement."
        ),
    )


# -----------------------------------------------------------------------------
# Processing pipeline
# -----------------------------------------------------------------------------

class ProducerWorkflowInputs(StrictModel):
    """Inputs/requirements needed to reproduce or build the dataset itself.

    These are producer-side workflow dependencies, not downstream application requirements.
    """

    data_types: Optional[List[str]] = None
    sources: Optional[List[str]] = None
    variables_or_covariates: Optional[List[str]] = None
    temporal_requirements: Optional[List[str]] = None
    spatial_requirements: Optional[List[str]] = None
    method_specific_requirements: Optional[List[str]] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Processing pipeline
# -----------------------------------------------------------------------------

class ProcessingPipeline(StrictModel):
    preprocessing_steps: Optional[List[str]] = None
    feature_extraction_or_index_definition: Optional[List[str]] = None
    aggregation_methods: Optional[List[str]] = None
    model_fitting: Optional[List[str]] = None
    postprocessing: Optional[List[str]] = None
    selection_or_tuning_steps: Optional[List[str]] = Field(
        None,
        description="Model/filter/parameter selection steps; keep distinct from independent validation.",
    )
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Data quality and uncertainty
# -----------------------------------------------------------------------------

class AccuracyRequirements(StrictModel):
    measurement_accuracy: Optional[str] = None
    classification_accuracy: Optional[str] = None
    temporal_accuracy: Optional[str] = None
    spatial_accuracy: Optional[str] = None
    evidence: Optional[List[EvidenceRecord]] = None


class CompletenessRequirements(StrictModel):
    missing_data_tolerance: Optional[str] = None
    coverage_requirements: Optional[str] = None
    evidence: Optional[List[EvidenceRecord]] = None


class ScaleSensitivity(StrictModel):
    spatial: Optional[str] = None
    temporal: Optional[str] = None
    aggregation: Optional[str] = None
    evidence: Optional[List[EvidenceRecord]] = None


class MetricContext(str, Enum):
    selection_or_tuning = "selection_or_tuning"
    in_sample_diagnostic = "in_sample_diagnostic"
    out_of_sample_validation = "out_of_sample_validation"
    cross_validated_interval_assessment = "cross_validated_interval_assessment"
    spatial_uncertainty_summary = "spatial_uncertainty_summary"
    downstream_use_case = "downstream_use_case"
    other = "other"


class UncertaintyScope(str, Enum):
    per_pixel_or_local = "per_pixel_or_local"
    spatial_surface_summary = "spatial_surface_summary"
    station_or_observation = "station_or_observation"
    dataset_summary = "dataset_summary"
    propagated_use_case = "propagated_use_case"
    unknown = "unknown"


class EvaluationSupport(str, Enum):
    held_out_station = "held_out_station"
    observation_station = "observation_station"
    raster_cell = "raster_cell"
    spatial_surface = "spatial_surface"
    region = "region"
    use_case = "use_case"
    unknown = "unknown"


class UncertaintyEvidence(StrictModel):
    name: str
    scope: UncertaintyScope = UncertaintyScope.unknown
    assessment_context: Optional[MetricContext] = Field(
        None,
        description=(
            "Assessment context when this uncertainty evidence is an evaluated diagnostic, "
            "for example cross-validated prediction-interval assessment. Keep separate from spatial/support scope."
        ),
    )
    evaluation_support: EvaluationSupport = Field(
        EvaluationSupport.unknown,
        description=(
            "Where the quantity is evaluated or represented, e.g. held-out stations or raster cells. "
            "Do not use dataset/crop summary scope as a substitute for evaluation support."
        ),
    )
    evaluation_population: Optional[str] = Field(
        None,
        description="Human-readable description of the evaluated population/locations, when stated by the source.",
    )
    representation: Optional[str] = Field(
        None,
        description="Raster, table, scalar summary, interval metric, etc.",
    )
    interpretation: Optional[str] = None
    not_equivalent_to: Optional[List[str]] = Field(
        None,
        description="Important non-equivalences explicitly stated by the source, e.g. BSE is not a predictive interval.",
    )
    evidence: Optional[List[EvidenceRecord]] = None


class UncertaintyHandling(StrictModel):
    # Backward-compatible summary fields used by the earlier cross-paper pipeline.
    # Prefer `products_or_measures` for publication-grade semantics.
    global_: Optional[List[str]] = Field(
        None,
        alias="global",
        description="Legacy/global uncertainty summary. Do not place local raster products here.",
    )
    local: Optional[List[str]] = Field(
        None,
        description="Legacy/local uncertainty summary. Prefer products_or_measures with an explicit scope.",
    )
    products_or_measures: Optional[List[UncertaintyEvidence]] = None
    propagation_methods: Optional[List[str]] = None
    unpropagated_sources: Optional[List[str]] = None


class DataQualityDependencies(StrictModel):
    accuracy_requirements: Optional[AccuracyRequirements] = None
    completeness: Optional[CompletenessRequirements] = None
    consistency: Optional[List[str]] = None
    uncertainty_handling: Optional[UncertaintyHandling] = None
    outlier_sensitivity: Optional[str] = None
    scale_sensitivity: Optional[ScaleSensitivity] = None
    quality_dimensions: Optional[List[str]] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Validation and diagnostics
# -----------------------------------------------------------------------------

class MetricScope(str, Enum):
    # Generic scopes (preferred for cross-paper use when a domain-specific scope does not apply).
    dataset = "dataset"
    dataset_family = "dataset_family"
    subgroup = "subgroup"
    variable_or_layer = "variable_or_layer"
    study_or_site = "study_or_site"
    experiment_or_treatment = "experiment_or_treatment"
    model_or_method = "model_or_method"
    spatial_subset = "spatial_subset"
    temporal_subset = "temporal_subset"
    spatiotemporal_subset = "spatiotemporal_subset"
    use_case = "use_case"

    # Domain-specific convenience scopes retained for backwards compatibility.
    crop = "crop"
    crop_phase = "crop_phase"
    phase_year = "phase_year"

    # Legacy/support-like scopes. Prefer evaluation_support for physical/statistical support.
    local_or_pixel = "local_or_pixel"
    spatial_surface_summary = "spatial_surface_summary"
    station_or_observation = "station_or_observation"
    unknown = "unknown"


class MetricValueKind(str, Enum):
    scalar = "scalar"
    range = "range"
    estimate_with_range = "estimate_with_range"
    estimate_with_uncertainty = "estimate_with_uncertainty"
    labeled_series = "labeled_series"
    text_summary = "text_summary"


class AggregationStatistic(str, Enum):
    raw = "raw"
    mean = "mean"
    median = "median"
    minimum = "minimum"
    maximum = "maximum"
    count = "count"
    proportion = "proportion"
    other = "other"
    unspecified = "unspecified"


class NumericPointRole(str, Enum):
    start = "start"
    end = "end"
    observation = "observation"
    other = "other"


class UncertaintyScale(str, Enum):
    one_sigma = "one_sigma"
    standard_error = "standard_error"
    confidence_interval = "confidence_interval"
    prediction_interval = "prediction_interval"
    component_only = "component_only"
    unknown = "unknown"


class NumericPoint(StrictModel):
    label: str = Field(..., min_length=1)
    value: JsonNumberDecimal
    role: NumericPointRole = NumericPointRole.observation


class UncertaintyComponent(StrictModel):
    name: str = Field(..., min_length=1)
    value: NonNegativeJsonNumberDecimal
    unit_code: Optional[str] = None
    scale: UncertaintyScale = UncertaintyScale.unknown
    note: Optional[str] = None


class QuantitativeMetricValue(StrictModel):
    """Machine-comparable quantitative value while preserving the source rendering."""

    kind: MetricValueKind
    as_reported: str = Field(..., min_length=1)
    numeric_value: Optional[JsonNumberDecimal] = None
    lower_bound: Optional[JsonNumberDecimal] = None
    upper_bound: Optional[JsonNumberDecimal] = None
    nominal_level: Optional[UnitIntervalJsonNumberDecimal] = None
    unit_code: Optional[str] = Field(
        None,
        description="Canonical unit code, preferably UCUM; retain the source wording in the parent unit field.",
    )
    aggregation: AggregationStatistic = AggregationStatistic.unspecified
    range_basis: Optional[str] = Field(
        None,
        description="Meaning of bounds, e.g. min-max, confidence interval, prediction interval, or across-target range.",
    )
    points: Optional[List[NumericPoint]] = None
    uncertainty_components: Optional[List[UncertaintyComponent]] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_incomplete_shape(cls, value):
        """Choose the strongest shape supported by fields actually returned.

        Structured Outputs enforces the JSON Schema, but relationships such as
        "estimate_with_range requires a central value and two bounds" are Pydantic
        cross-field rules. A model can therefore return schema-valid JSON that the
        SDK rejects during ``responses.parse``. This pre-validator never invents a
        number: it only corrects ``kind`` from the fields that are present, and
        clears a lone unusable bound so the authored ``as_reported`` text can be
        reparsed or sent through semantic repair later.
        """

        if not isinstance(value, dict):
            return value
        data = dict(value)
        kind_value = data.get("kind")
        kind = getattr(kind_value, "value", kind_value)
        numeric = data.get("numeric_value")
        lower = data.get("lower_bound")
        upper = data.get("upper_bound")
        points = data.get("points") or []
        components = data.get("uncertainty_components") or []
        complete_bounds = lower is not None and upper is not None
        partial_bounds = (lower is None) != (upper is None)

        if partial_bounds:
            data["lower_bound"] = None
            data["upper_bound"] = None
            complete_bounds = False

        if numeric is not None and complete_bounds:
            data["kind"] = MetricValueKind.estimate_with_range.value
        elif numeric is not None and components:
            data["kind"] = MetricValueKind.estimate_with_uncertainty.value
        elif numeric is not None:
            data["kind"] = MetricValueKind.scalar.value
        elif complete_bounds:
            data["kind"] = MetricValueKind.range.value
        elif points:
            data["kind"] = MetricValueKind.labeled_series.value
        elif kind in {
            MetricValueKind.scalar.value,
            MetricValueKind.range.value,
            MetricValueKind.estimate_with_range.value,
            MetricValueKind.estimate_with_uncertainty.value,
            MetricValueKind.labeled_series.value,
        }:
            data["kind"] = MetricValueKind.text_summary.value
        return data

    @model_validator(mode="after")
    def validate_shape(self) -> "QuantitativeMetricValue":
        if self.lower_bound is not None and self.upper_bound is not None and self.lower_bound > self.upper_bound:
            raise ValueError("lower_bound must be less than or equal to upper_bound")
        if (self.lower_bound is None) != (self.upper_bound is None):
            raise ValueError("lower_bound and upper_bound must be supplied together")
        if self.kind == MetricValueKind.scalar and self.numeric_value is None:
            raise ValueError("scalar values require numeric_value")
        if self.kind == MetricValueKind.range and self.lower_bound is None:
            raise ValueError("range values require lower_bound and upper_bound")
        if self.kind == MetricValueKind.estimate_with_range and (
            self.numeric_value is None or self.lower_bound is None
        ):
            raise ValueError("estimate_with_range requires numeric_value and both bounds")
        if self.kind == MetricValueKind.estimate_with_uncertainty and (
            self.numeric_value is None or not self.uncertainty_components
        ):
            raise ValueError("estimate_with_uncertainty requires numeric_value and uncertainty_components")
        if self.kind == MetricValueKind.labeled_series and not self.points:
            raise ValueError("labeled_series requires points")
        return self


class TuningParameterRecord(StrictModel):
    """Quantitative model/workflow setting, kept separate from validation metrics."""

    name: str
    value_or_summary: Optional[str] = None
    unit: Optional[str] = None
    quantitative_value: Optional[QuantitativeMetricValue] = Field(
        None,
        description="Structured numeric representation for validation and comparison; value_or_summary remains the display value.",
    )
    scope: MetricScope = MetricScope.unknown
    scope_label: Optional[str] = None
    interpretation: Optional[str] = None
    evidence: List[EvidenceRecord] = Field(
        ..., min_length=1,
        description="At least one source-grounded evidence record for the reported tuning parameter or setting.",
    )


class ValidationMetricRecord(StrictModel):
    name: str
    value_or_summary: Optional[str] = None
    unit: Optional[str] = None
    quantitative_value: Optional[QuantitativeMetricValue] = Field(
        None,
        description="Structured numeric representation for validation and comparison; value_or_summary remains the display value.",
    )
    context: MetricContext
    scope: MetricScope = Field(
        MetricScope.unknown,
        description="Summary/aggregation scope of the reported metric, not the locations where it was evaluated.",
    )
    scope_label: Optional[str] = Field(
        None,
        description=(
            "Exactly one scientific target label corresponding to this record's summary scope. Do not combine multiple "
            "targets in one label. Use the most specific domain-appropriate scope (dataset, dataset_family, subgroup, "
            "variable_or_layer, study_or_site, experiment_or_treatment, model_or_method, spatial/temporal subset, use_case, "
            "or an available domain-specific scope). Keep physical/statistical evaluation support separate."
        ),
    )
    evaluation_support: EvaluationSupport = Field(
        EvaluationSupport.unknown,
        description=(
            "Where the metric was evaluated, e.g. held-out observations, sites, raster cells, regions, or another support. "
            "Do not generalize performance from the observed/evaluated support to unevaluated supports unless the source demonstrates it."
        ),
    )
    evaluation_population: Optional[str] = Field(
        None,
        description="Human-readable population/locations used to evaluate the metric, when supported by the source.",
    )
    interpretation: Optional[str] = None
    evidence: List[EvidenceRecord] = Field(
        ...,
        min_length=1,
        description=(
            "At least one source-grounded evidence record is required for every canonical quantitative metric. "
            "If the source supports a numerical metric, do not emit the metric without its evidence."
        ),
    )


class ValidationAndDiagnostics(StrictModel):
    validation_method: Optional[List[str]] = None
    validation_metrics: Optional[List[ValidationMetricRecord]] = Field(
        None,
        description=(
            "Canonical quantitative metric ledger for the record. Use one metric record per metric, target, "
            "and aggregation scope. Include producer validation metrics, spatial uncertainty summaries, "
            "cross-validated interval diagnostics, and demonstrated downstream quantitative metrics here. "
            "Do not combine crop-level and dataset-family values in one record."
        ),
    )
    tuning_parameters: Optional[List[TuningParameterRecord]] = Field(
        None,
        description=(
            "Quantitative tuning parameters/settings used or evaluated by the workflow. "
            "Keep these separate from validation/performance metrics even when they occur in selection tables."
        ),
    )
    model_fit_diagnostics: Optional[List[str]] = None
    plausibility_checks: Optional[List[str]] = None
    validation_limitations: Optional[List[str]] = Field(
        None,
        description="Limitations of validation design, e.g. conditioning on prior filtering or spatial dependence.",
    )
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Outputs and producer-side fitness evidence
# -----------------------------------------------------------------------------

class FitnessForUseMetrics(StrictModel):
    producer_side_metrics: Optional[List[ValidationMetricRecord]] = Field(
        None,
        description=(
            "Derived deterministically by the pipeline from validation_and_diagnostics.validation_metrics. "
            "The extraction model should leave this null rather than independently re-generating duplicate metric values."
        ),
    )
    application_specific_metrics: Optional[List[ValidationMetricRecord]] = Field(
        None,
        description=(
            "Derived deterministically by the pipeline from canonical validation metrics whose context is downstream_use_case. "
            "The extraction model should leave this null rather than independently duplicating values."
        ),
    )
    formal_thresholds: Optional[List[str]] = Field(
        None,
        description=(
            "Only thresholds explicitly defined by the source as formal acceptability/suitability criteria. "
            "Illustrative reference levels must not be placed here."
        ),
    )


class OutputsAndFitnessIndicators(StrictModel):
    primary_outputs: Optional[List[str]] = None
    secondary_outputs: Optional[List[str]] = None
    fitness_for_use_metrics: Optional[FitnessForUseMetrics] = None
    interpretability: Optional[str] = None
    transferability: Optional[str] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Limitations and risks
# -----------------------------------------------------------------------------

class LimitationsAndRisks(StrictModel):
    known_limitations: Optional[List[str]] = None
    risk_of_misuse: Optional[List[str]] = None
    bias_sources: Optional[List[str]] = None
    extrapolation_limits: Optional[List[str]] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Reproducibility / FAIRness
# -----------------------------------------------------------------------------

class FAIRCompliance(StrictModel):
    findable: Optional[str] = None
    accessible: Optional[str] = None
    interoperable: Optional[str] = None
    reusable: Optional[str] = None


class ReproducibilityAndFairness(StrictModel):
    data_availability: Optional[str] = None
    software_tools: Optional[List[str]] = None
    workflow_transparency: Optional[str] = None
    fair_compliance: Optional[FAIRCompliance] = None
    machine_actionable_metadata: Optional[List[str]] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Scalability / operations
# -----------------------------------------------------------------------------

class ScalabilityAndOperationalAspects(StrictModel):
    data_volume: Optional[str] = None
    computational_scalability: Optional[str] = None
    automation_potential: Optional[str] = None
    update_or_maintenance_model: Optional[str] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Decision-oriented layers
# Keep the old UI-compatible fields, but add strict provenance/basis controls.
# -----------------------------------------------------------------------------

class ValidationStrength(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class OperationalReadiness(str, Enum):
    research = "research"
    pilot = "pilot"
    operational = "operational"
    unknown = "unknown"


class TransferabilityRisk(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    unknown = "unknown"


class FitnessAssessmentBasis(str, Enum):
    unassessed = "unassessed"
    source_explicit = "source_explicit"
    declared_rule = "declared_rule"
    human_review = "human_review"


class DecisionRiskProfile(StrictModel):
    critical_quality_dimensions: Optional[List[str]] = None
    failure_modes: Optional[List[str]] = None
    consequences_of_misuse: Optional[List[str]] = None
    acceptable_uncertainty_levels: Optional[str] = Field(
        None,
        description=(
            "Populate ONLY if the source explicitly defines an acceptable uncertainty threshold "
            "or a declared external decision rule supplies one. Do NOT convert illustrative values "
            "or typical observed errors into acceptability thresholds. Otherwise null."
        ),
    )
    acceptable_uncertainty_is_formal: Optional[bool] = Field(
        None,
        description="True only when the threshold is explicitly formal/decision-relevant in the source or declared rule.",
    )
    evidence: Optional[List[EvidenceRecord]] = None


class FitnessClassification(StrictModel):
    suitable_for: Optional[List[str]] = Field(
        None,
        description=(
            "Populate only when suitability is explicitly stated by the source, follows a declared deterministic rule, "
            "or has been human-reviewed. Otherwise null."
        ),
    )
    conditionally_suitable_for: Optional[List[str]] = Field(
        None,
        description="Same rule as suitable_for; include the required conditions explicitly.",
    )
    not_recommended_for: Optional[List[str]] = Field(
        None,
        description="Populate only when directly supported by source evidence/rule/human review; otherwise null.",
    )
    assessment_basis: FitnessAssessmentBasis = FitnessAssessmentBasis.unassessed
    decision_rule: Optional[str] = Field(
        None,
        description="Formal rule used to derive the classification, if any. Null for pure extraction.",
    )
    evidence: Optional[List[EvidenceRecord]] = None


class EvidenceAndMaturity(StrictModel):
    validation_strength: Optional[ValidationStrength] = Field(
        None,
        description=(
            "Do not assign high/medium/low from general impression. Populate only when source explicit, "
            "a declared rubric is supplied, or human-reviewed; otherwise 'unknown' or null."
        ),
    )
    operational_readiness: Optional[OperationalReadiness] = Field(
        None,
        description="Do not infer operational readiness from data availability alone; use unknown/null unless supported by criteria.",
    )
    transferability_risk: Optional[TransferabilityRisk] = Field(
        None,
        description="Use unknown/null unless supported by explicit source evidence, a declared rubric, or human review.",
    )
    assessment_basis: FitnessAssessmentBasis = FitnessAssessmentBasis.unassessed
    evidence_notes: Optional[str] = None
    evidence: Optional[List[EvidenceRecord]] = None


# -----------------------------------------------------------------------------
# Extraction provenance
# -----------------------------------------------------------------------------

class ExtractionScope(str, Enum):
    producer_data_document = "producer_data_document"
    downstream_application_paper = "downstream_application_paper"
    methods_document = "methods_document"
    repository_documentation = "repository_documentation"
    other = "other"


class RunPurpose(str, Enum):
    test = "test"
    evaluation = "evaluation"
    publication = "publication"


class ExtractionProvenance(StrictModel):
    schema_version: Optional[str] = Field(
        None,
        description="DFFP application-matrix schema version injected by the pipeline.",
    )
    canonical_source_sha256: Optional[str] = Field(
        None,
        pattern=r"^[0-9a-fA-F]{64}$",
        description=(
            "SHA-256 of the canonical PDF injected from the scientific source package. "
            "This is the primary identity check when filenames differ."
        ),
    )
    source_bundle_sha256: Optional[str] = Field(
        None,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="SHA-256 of the exact provenance-aware source bundle used for extraction.",
    )
    run_purpose: RunPurpose = Field(
        RunPurpose.evaluation,
        description="Declared intent of this run. The configured extractor injects this value; test output must not pass the publication gate.",
    )
    extraction_scope: Optional[ExtractionScope] = None
    source_fidelity_status: Optional[str] = Field(
        None, description="Effective deterministic source-package fidelity status after any configured recovery."
    )
    source_fidelity_initial_status: Optional[str] = Field(
        None, description="Initial deterministic source-fidelity audit status before multimodal recovery."
    )
    unresolved_source_items: Optional[List[str]] = Field(
        None, description="Known conversion/recovery gaps. Their absence from extracted evidence must not be interpreted as absence from the PDF."
    )
    explicitly_stated: Optional[List[str]] = None
    inferred: Optional[List[str]] = None
    derived: Optional[List[str]] = None
    assumed: Optional[List[str]] = None
    unresolved_or_missing: Optional[List[str]] = None
    evidence_records: Optional[List[EvidenceRecord]] = None
    review_status: ReviewStatus = ReviewStatus.machine_extracted


# -----------------------------------------------------------------------------
# FINAL FAIRagro application / DFFP evidence model
# -----------------------------------------------------------------------------

class FAIRagroApplicationDataFitnessModel(StrictModel):
    document_metadata: Optional[DocumentMetadata] = None

    # What the data product is / provides
    dataset_characteristics: Optional[DatasetCharacteristics] = None

    # What the source says about uses and downstream application requirements
    application_profile: Optional[ApplicationProfile] = None
    analysis_characteristics: Optional[AnalysisCharacteristics] = None
    input_data_requirements: Optional[InputDataRequirements] = Field(
        None,
        description=(
            "DOWNSTREAM application requirements only: what a user needs to apply/use the already-published dataset. "
            "Inputs required only to build or reproduce the dataset belong in producer_workflow_inputs."
        ),
    )

    # Producer-side inputs needed to build/reproduce the dataset. Keep separate
    # from downstream application requirements above.
    producer_workflow_inputs: Optional[ProducerWorkflowInputs] = None

    # How the data/method are produced and evaluated
    processing_pipeline: Optional[ProcessingPipeline] = None
    data_quality_dependencies: Optional[DataQualityDependencies] = None
    validation_and_diagnostics: Optional[ValidationAndDiagnostics] = None
    outputs_and_fitness_indicators: Optional[OutputsAndFitnessIndicators] = None
    limitations_and_risks: Optional[LimitationsAndRisks] = None
    reproducibility_and_fairness: Optional[ReproducibilityAndFairness] = None
    scalability_and_operational_aspects: Optional[ScalabilityAndOperationalAspects] = None

    # Decision-oriented layers. For producer-document extraction these should
    # usually stay unassessed unless the source/rules explicitly support them.
    decision_risk_profile: Optional[DecisionRiskProfile] = None
    fitness_classification: Optional[FitnessClassification] = None
    evidence_and_maturity: Optional[EvidenceAndMaturity] = None

    # Transparency for AI-assisted extraction
    extraction_provenance: Optional[ExtractionProvenance] = None
