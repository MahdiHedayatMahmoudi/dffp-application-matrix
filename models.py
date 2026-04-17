#models.py → all Pydantic models & Enums

from enum import Enum
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict
from datetime import date




# --------------------
#📄 Document Metadata
# --------------------
class DocumentMetadata(BaseModel):
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    doi: Optional[str] = None
    publication_year: Optional[int] = None
    source: Optional[str] = None
    keywords: Optional[List[str]] = None


# --------------------
#🎯 Application Profile
# --------------------
class ApplicationProfile(BaseModel):
    application_area: Optional[List[str]] = None
    intended_use: Optional[str] = None
    use_case_problem: Optional[List[str]] = None
    beneficiaries: Optional[List[str]] = None
    recommended_context: Optional[List[str]] = None
    not_suitable_for: Optional[List[str]] = None

# --------------------
#🧠 Analysis Characteristics
# --------------------
class AnalysisType(str, Enum):
    classification = "classification"
    regression = "regression"
    time_series = "time-series analysis"
    phenology_indices = "phenology-aware indices"
    uncertainty = "uncertainty estimation"
    other = "other"


class AnalysisCharacteristics(BaseModel):
    analysis_type: Optional[List[AnalysisType]] = None
    modeling_approach: Optional[List[str]] = None
    assumptions: Optional[List[str]] = None
    sensitivity_factors: Optional[List[str]] = None

# --------------------
#📥 Input Data Requirements
# --------------------
class SpatialResolution(BaseModel):
    minimum: Optional[str] = None
    recommended: Optional[str] = None
    aggregation_levels: Optional[List[str]] = None


class TemporalResolution(BaseModel):
    sampling_frequency: Optional[str] = None
    temporal_extent: Optional[str] = None
    phenology_dependency: Optional[bool] = None


class InputDataRequirements(BaseModel):
    data_types: Optional[List[str]] = None
    required_sources: Optional[List[str]] = None
    spatial_resolution: Optional[SpatialResolution] = None
    temporal_resolution: Optional[TemporalResolution] = None
    spectral_or_variable_requirements: Optional[List[str]] = None
    minimum_sample_size: Optional[str] = None
    dependency_on_multiple_inputs: Optional[bool] = None

# --------------------
#⚙️ Processing Pipeline
# --------------------
class ProcessingPipeline(BaseModel):
    preprocessing_steps: Optional[List[str]] = None
    feature_extraction_or_index_definition: Optional[List[str]] = None
    aggregation_methods: Optional[List[str]] = None
    model_fitting: Optional[List[str]] = None
    postprocessing: Optional[List[str]] = None

# --------------------
#📊 Data Quality Dependencies
# --------------------
class AccuracyRequirements(BaseModel):
    measurement_accuracy: Optional[str] = None
    classification_accuracy: Optional[str] = None


class CompletenessRequirements(BaseModel):
    missing_data_tolerance: Optional[str] = None
    coverage_requirements: Optional[str] = None


class ScaleSensitivity(BaseModel):
    spatial: Optional[str] = None
    temporal: Optional[str] = None
    aggregation: Optional[str] = None


class UncertaintyHandling(BaseModel):
    global_: Optional[List[str]] = Field(None, alias="global")
    local: Optional[List[str]] = None


class DataQualityDependencies(BaseModel):
    accuracy_requirements: Optional[AccuracyRequirements] = None
    completeness: Optional[CompletenessRequirements] = None
    consistency: Optional[List[str]] = None
    uncertainty_handling: Optional[UncertaintyHandling] = None
    outlier_sensitivity: Optional[str] = None
    scale_sensitivity: Optional[ScaleSensitivity] = None

# --------------------
#✅ Validation & Diagnostics
# --------------------
class ValidationMetrics(BaseModel):
    global_: Optional[List[str]] = Field(None, alias="global")
    local: Optional[List[str]] = None


class ValidationAndDiagnostics(BaseModel):
    validation_method: Optional[List[str]] = None
    validation_metrics: Optional[ValidationMetrics] = None
    model_fit_diagnostics: Optional[List[str]] = None
    plausibility_checks: Optional[List[str]] = None

# --------------------
#📤 Outputs & Fitness Indicators
# --------------------
class FitnessForUseMetrics(BaseModel):
    global_: Optional[List[str]] = Field(None, alias="global")
    local: Optional[List[str]] = None


class OutputsAndFitnessIndicators(BaseModel):
    primary_outputs: Optional[List[str]] = None
    secondary_outputs: Optional[List[str]] = None
    fitness_for_use_metrics: Optional[FitnessForUseMetrics] = None
    interpretability: Optional[str] = None
    transferability: Optional[str] = None

# --------------------
#⚠️ Limitations & Risks
# --------------------

class LimitationsAndRisks(BaseModel):
    known_limitations: Optional[List[str]] = None
    risk_of_misuse: Optional[List[str]] = None
    bias_sources: Optional[List[str]] = None

# --------------------
#🔁 Reproducibility & FAIRness
# --------------------

class FAIRCompliance(BaseModel):
    findable: Optional[str] = None
    accessible: Optional[str] = None
    interoperable: Optional[str] = None
    reusable: Optional[str] = None


class ReproducibilityAndFairness(BaseModel):
    data_availability: Optional[str] = None
    software_tools: Optional[List[str]] = None
    workflow_transparency: Optional[str] = None
    fair_compliance: Optional[FAIRCompliance] = None

# --------------------
#📈 Scalability & Operations
# --------------------
class ScalabilityAndOperationalAspects(BaseModel):
    data_volume: Optional[str] = None
    computational_scalability: Optional[str] = None
    automation_potential: Optional[str] = None




# --------------------
# 🎚️ Evidence & Fitness Enums
# --------------------

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


# --------------------
# 🚨 Decision Risk Profile (User-Centric)
# --------------------

class DecisionRiskProfile(BaseModel):
    critical_quality_dimensions: Optional[List[str]] = None
    failure_modes: Optional[List[str]] = None
    consequences_of_misuse: Optional[List[str]] = None
    acceptable_uncertainty_levels: Optional[str] = None


# --------------------
# 🎯 Fitness-for-Purpose Classification
# --------------------

class FitnessClassification(BaseModel):
    suitable_for: Optional[List[str]] = None
    conditionally_suitable_for: Optional[List[str]] = None
    not_recommended_for: Optional[List[str]] = None


# --------------------
# 🧪 Evidence Strength & Maturity
# --------------------

class EvidenceAndMaturity(BaseModel):
    validation_strength: Optional[ValidationStrength] = None
    operational_readiness: Optional[OperationalReadiness] = None
    transferability_risk: Optional[TransferabilityRisk] = None
    evidence_notes: Optional[str] = None


# --------------------
# 🧾 Extraction Provenance
# --------------------

class ExtractionProvenance(BaseModel):
    explicitly_stated: Optional[List[str]] = None
    inferred: Optional[List[str]] = None
    assumed: Optional[List[str]] = None
















# --------------------
# 🧠 DFFP (Data-Fitness-for-Purpose) Models
# --------------------

class DFFPCategory(BaseModel):
    name: str
    description: str
    why_it_matters: Optional[str] = None


class DFFPFitnessLevel(str, Enum):
    high = "High"
    moderate = "Moderate"
    low = "Low"
    unrealized = "Unrealized"


class DFFPFitnessEntry(BaseModel):
    paper: str
    fitness: DFFPFitnessLevel
    reasoning: Optional[str] = None
    evidence: Optional[List[str]] = None





class DFFPMatrixEntry(BaseModel):
    category: str
    fitness: List[DFFPFitnessEntry]  
    explanation: Optional[str] = None




class DFFPDataset(BaseModel):
    name: str
    description: str
    papers: List[str]


class DFFPResult(BaseModel):
    dataset: List[DFFPDataset]
    categories: List[DFFPCategory]
    matrix: List[DFFPMatrixEntry]
    narrative: Optional[str] = None



# --------------------
#🌾 FINAL: FAIRagro Measure-3.3 Model
# --------------------

class FAIRagroApplicationDataFitnessModel(BaseModel):
    document_metadata: Optional[DocumentMetadata] = None
    application_profile: Optional[ApplicationProfile] = None
    analysis_characteristics: Optional[AnalysisCharacteristics] = None
    input_data_requirements: Optional[InputDataRequirements] = None
    processing_pipeline: Optional[ProcessingPipeline] = None
    data_quality_dependencies: Optional[DataQualityDependencies] = None
    validation_and_diagnostics: Optional[ValidationAndDiagnostics] = None
    outputs_and_fitness_indicators: Optional[OutputsAndFitnessIndicators] = None
    limitations_and_risks: Optional[LimitationsAndRisks] = None
    reproducibility_and_fairness: Optional[ReproducibilityAndFairness] = None
    scalability_and_operational_aspects: Optional[ScalabilityAndOperationalAspects] = None

    # --- User- & decision-oriented layers
    decision_risk_profile: Optional[DecisionRiskProfile] = None
    fitness_classification: Optional[FitnessClassification] = None
    evidence_and_maturity: Optional[EvidenceAndMaturity] = None

    # --- OPTIONAL: transparency for AI-assisted extraction
    extraction_provenance: Optional[ExtractionProvenance] = None



