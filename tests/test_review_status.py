from review_utils import force_machine_review_status
from models import (
    DatasetCharacteristics,
    EvidenceRecord,
    EvidenceType,
    ExtractionProvenance,
    FAIRagroApplicationDataFitnessModel,
    ReviewStatus,
)


def test_llm_cannot_self_assert_human_review_status():
    record = FAIRagroApplicationDataFitnessModel(
        dataset_characteristics=DatasetCharacteristics(
            evidence=[
                EvidenceRecord(
                    claim="A claim",
                    evidence_type=EvidenceType.explicit,
                    source_text="A source statement",
                    review_status=ReviewStatus.human_verified,
                )
            ]
        ),
        extraction_provenance=ExtractionProvenance(
            review_status=ReviewStatus.human_corrected,
        ),
    )

    force_machine_review_status(record)

    assert record.dataset_characteristics.evidence[0].review_status == ReviewStatus.machine_extracted
    assert record.extraction_provenance.review_status == ReviewStatus.machine_extracted
