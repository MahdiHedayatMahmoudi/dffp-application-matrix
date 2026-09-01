"""Deterministic evidence-locator normalization from the scientific source package.

Structured source-item IDs are authoritative only for structured evidence. Ordinary
prose must not inherit the page/label of a nearby table, figure or formula merely
because the LLM attached that ID. This keeps provenance portable across document
layouts and prevents adjacency artefacts from becoming source claims.
"""

from __future__ import annotations

from typing import Any

from models import FAIRagroApplicationDataFitnessModel


_ALLOWED_MODALITIES_BY_ITEM = {
    "table": {"author_table"},
    "figure": {"author_figure", "author_caption"},
    "formula": {"author_formula"},
}


def _is_structured_evidence(value: dict[str, Any], meta: dict[str, Any]) -> bool:
    item_type = str(meta.get("item_type") or "")
    modality = str(value.get("source_modality") or "unknown")
    representation = str(value.get("representation_method") or "unknown")
    allowed = _ALLOWED_MODALITIES_BY_ITEM.get(item_type, set())
    if modality in allowed:
        return True
    # Structured representations may occasionally arrive with modality=unknown;
    # accept only when the representation itself is explicitly structured.
    if modality == "unknown" and representation in {"docling_structured", "structured_visual_recovery"}:
        return True
    return False


def normalize_evidence_locators(
    record: FAIRagroApplicationDataFitnessModel,
    source_item_registry: dict[str, dict[str, Any]] | None,
) -> FAIRagroApplicationDataFitnessModel:
    """Normalize structured evidence locators and remove inappropriate item links.

    Rules are document-agnostic:
    - authored prose keeps its own prose locator and never inherits a structured item;
    - table/figure/formula evidence may inherit deterministic page/location/section;
    - source_item_id/modality mismatches are cleared rather than converted into a
      misleading structured citation.
    """
    registry = source_item_registry or {}
    if not registry:
        return record

    payload = record.model_dump(mode="json", by_alias=True, exclude_none=False)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            iid = value.get("source_item_id")
            if iid and iid in registry and "claim" in value and "evidence_type" in value:
                meta = registry[iid]
                if _is_structured_evidence(value, meta):
                    page = meta.get("source_page")
                    location = meta.get("source_location")
                    section = meta.get("section_hint")
                    if page is not None:
                        value["source_page"] = page
                    if location:
                        value["source_location"] = location
                    if section:
                        value["source_section"] = section
                else:
                    # source_item_id is not a generic location handle. Clear it for
                    # prose or modality/item mismatches so nearby formulas/figures do
                    # not overwrite valid prose provenance.
                    value["source_item_id"] = None
                    value["source_asset_sha256"] = None
                    value["representation_confidence"] = None
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return FAIRagroApplicationDataFitnessModel.model_validate(payload)
