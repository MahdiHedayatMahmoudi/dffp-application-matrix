"""Deterministic review-state helpers.

Human-review states must never be self-asserted by the extraction model. This
module intentionally has no OpenAI dependency so it can be unit-tested in a
lightweight environment.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from models import (
    EvidenceRecord,
    ExtractionProvenance,
    FAIRagroApplicationDataFitnessModel,
    ReviewStatus,
)


def force_machine_review_status(record: FAIRagroApplicationDataFitnessModel) -> None:
    """Reset every extracted review state to ``machine_extracted``.

    A separate human-review workflow may later promote records to
    ``human_verified`` or ``human_corrected``. The extraction LLM itself is
    never allowed to certify those states.
    """

    def walk(value: Any) -> None:
        if isinstance(value, EvidenceRecord):
            value.review_status = ReviewStatus.machine_extracted
        if isinstance(value, BaseModel):
            for field_name in value.__class__.model_fields:
                walk(getattr(value, field_name, None))
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, tuple):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(record)
    if record.extraction_provenance is None:
        record.extraction_provenance = ExtractionProvenance()
    record.extraction_provenance.review_status = ReviewStatus.machine_extracted
