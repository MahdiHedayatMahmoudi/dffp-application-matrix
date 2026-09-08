from models import FAIRagroApplicationDataFitnessModel
from openai_schema import (
    OpenAISchemaCompatibilityError,
    strict_schema_from_pydantic,
    validate_openai_schema_compatibility,
)


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


def test_openai_schema_has_no_regex_lookaround():
    """Structured Outputs supports only a restricted regular-expression dialect."""

    schema = strict_schema_from_pydantic(FAIRagroApplicationDataFitnessModel)
    lookaround_tokens = ("(?=", "(?!", "(?<=", "(?<!")
    offenders = [
        node["pattern"]
        for node in _walk(schema)
        if isinstance(node.get("pattern"), str)
        and any(token in node["pattern"] for token in lookaround_tokens)
    ]
    assert offenders == []


def test_schema_compatibility_preflight_accepts_release_schema():
    schema = validate_openai_schema_compatibility(FAIRagroApplicationDataFitnessModel)
    assert schema["$defs"]["NumericPoint"]["properties"]["value"] == {"type": "number"}


def test_schema_compatibility_preflight_reports_unsupported_lookaround():
    import pytest

    class ModelWithUnsupportedPattern:
        @staticmethod
        def model_json_schema(*, by_alias):
            assert by_alias is True
            return {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "pattern": r"^(?!forbidden$).+$"}
                },
            }

    with pytest.raises(OpenAISchemaCompatibilityError, match="regex lookaround") as exc_info:
        validate_openai_schema_compatibility(ModelWithUnsupportedPattern)

    assert "properties.value.pattern" in str(exc_info.value)


def test_decimal_fields_are_api_numbers_but_validate_as_decimal():
    from decimal import Decimal
    from models import MetricValueKind, NumericPoint, QuantitativeMetricValue, UncertaintyComponent

    schema = strict_schema_from_pydantic(FAIRagroApplicationDataFitnessModel)
    assert schema["$defs"]["NumericPoint"]["properties"]["value"] == {"type": "number"}
    assert schema["$defs"]["UncertaintyComponent"]["properties"]["value"] == {
        "minimum": 0,
        "type": "number",
    }

    point = NumericPoint(label="estimate", value=0.9)
    component = UncertaintyComponent(name="standard error", value="0.01")
    metric = QuantitativeMetricValue(
        kind=MetricValueKind.scalar,
        as_reported="0.900",
        numeric_value=0.9,
    )
    assert point.value == Decimal("0.9")
    assert component.value == Decimal("0.01")
    assert metric.numeric_value == Decimal("0.9")


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
