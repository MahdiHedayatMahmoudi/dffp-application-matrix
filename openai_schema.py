"""OpenAI Structured Outputs JSON-schema compatibility helpers.

The preferred extraction path in :mod:`llm_service` uses
``client.responses.parse(..., text_format=PydanticModel)`` so the OpenAI Python
SDK performs its own Pydantic-to-Structured-Outputs conversion.

This module remains as a compatibility fallback for SDK versions/endpoints that
only expose ``responses.create``.  Pydantic v2 may emit schemas like::

    {"$ref": "#/$defs/EvidenceType", "description": "..."}

JSON Schema itself permits siblings next to ``$ref`` in modern drafts, but the
Structured Outputs validator currently rejects that shape.  Therefore a node
containing ``$ref`` is reduced to the reference alone.  Descriptions remain on
non-reference nodes, so useful schema guidance is retained wherever the API
supports it.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _normalize(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize(x) for x in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        # Defaults/examples/titles are unnecessary for generation and can make
        # otherwise valid strict schemas less portable across API versions.
        if key in {"default", "examples", "title"}:
            continue
        out[key] = _normalize(value)

    # OpenAI Structured Outputs does not accept sibling keywords beside $ref
    # (e.g. {"$ref": ..., "description": ...}).  Preserve only the reference.
    if "$ref" in out:
        return {"$ref": out["$ref"]}

    # Strict Structured Outputs expects all declared object properties to be
    # required, while semantic optionality is represented with a nullable
    # anyOf/union.  It also requires additionalProperties=false.
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties") or {}
        out["additionalProperties"] = False
        out["required"] = list(props.keys())

    return out


def strict_schema_from_pydantic(model_cls) -> dict[str, Any]:
    """Return a conservative Structured-Outputs-compatible JSON schema.

    Prefer ``responses.parse(text_format=model_cls)`` when available.  This
    helper is intentionally kept for compatibility/fallback use and tests.
    """

    schema = model_cls.model_json_schema(by_alias=True)
    schema = _normalize(deepcopy(schema))
    schema.pop("title", None)
    return schema
