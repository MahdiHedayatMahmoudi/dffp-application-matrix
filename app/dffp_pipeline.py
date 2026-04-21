# dffp_pipeline.py

import json
from typing import List
from openai import OpenAI

from models import (
    FAIRagroApplicationDataFitnessModel,
    DFFPResult,
)


class DFFPPipeline:
    def __init__(self, client: OpenAI, model: str = "gpt-5-mini"):
        self.client = client
        self.model = model

    # ------------------------
    # ALIGN STEP
    # ------------------------
    def align_use_cases(
        self,
        use_cases: List[FAIRagroApplicationDataFitnessModel],
    ):
        aligned = []

        for uc in use_cases:
            aligned.append({
                "paper": uc.document_metadata.title if uc.document_metadata else "unknown",
                "use_case": uc.application_profile.intended_use if uc.application_profile else None,
                "application_area": uc.application_profile.application_area if uc.application_profile else None,
                "spatial_scale": (
                    uc.input_data_requirements.spatial_resolution.recommended
                    if uc.input_data_requirements and uc.input_data_requirements.spatial_resolution
                    else None
                ),
                "temporal_resolution": (
                    uc.input_data_requirements.temporal_resolution.temporal_extent
                    if uc.input_data_requirements and uc.input_data_requirements.temporal_resolution
                    else None
                ),
                "uncertainty": (
                    uc.data_quality_dependencies.uncertainty_handling.global_
                    if uc.data_quality_dependencies and uc.data_quality_dependencies.uncertainty_handling
                    else None
                ),
                "outputs": (
                    uc.outputs_and_fitness_indicators.primary_outputs
                    if uc.outputs_and_fitness_indicators
                    else None
                ),
            })

        return aligned

    # ------------------------
    # MAIN DFFP PIPELINE
    # ------------------------
    def run(
        self,
        use_cases: List[FAIRagroApplicationDataFitnessModel],
    ) -> DFFPResult:

        aligned = self.align_use_cases(use_cases)

        prompt = f"""
You are an expert in Data-Fitness-for-Purpose (DFFP).

INPUT:
{json.dumps(aligned, indent=2)}

TASK:
1. Detect shared dataset
2. Compare use cases
3. Identify tensions
4. Create DFFP categories
5. Build fitness matrix
6. Write narrative

OUTPUT JSON:
{{
  "dataset": [...],
  "categories": [...],
  "matrix": [...],
  "narrative": "..."
}}
"""

        response = self.client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=DFFPResult,
        )

        return response.choices[0].message.parsed