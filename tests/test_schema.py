from models import FAIRagroApplicationDataFitnessModel
from openai_schema import strict_schema_from_pydantic


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_openai_schema_requires_all_top_level_properties():
    schema = strict_schema_from_pydantic(FAIRagroApplicationDataFitnessModel)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_openai_schema_has_no_ref_sibling_keywords():
    schema = strict_schema_from_pydantic(FAIRagroApplicationDataFitnessModel)
    offenders = [node for node in _walk(schema) if "$ref" in node and set(node) != {"$ref"}]
    assert offenders == []


def test_v32_schema_uses_interval_assessment_not_calibration_enums():
    from models import AnalysisType, MetricContext, UncertaintyScope

    assert "cross_validated_interval_assessment" in {x.value for x in MetricContext}
    assert "cross_validated_interval_calibration" not in {x.value for x in MetricContext}
    assert "prediction_interval_calibration" not in {x.value for x in UncertaintyScope}
    assert "prediction-interval assessment" in {x.value for x in AnalysisType}
    assert "calibration" not in {x.value for x in AnalysisType}


def test_v321_schema_adds_crop_phase_scope():
    from models import MetricScope
    assert "crop_phase" in {x.value for x in MetricScope}
