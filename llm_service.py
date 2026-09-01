"""OpenAI Responses API adapter for structured FAIRagro / DFFP extraction.

Preferred path: ``client.responses.parse(..., text_format=PydanticModel)``.
This lets the official OpenAI Python SDK convert the Pydantic model to the
supported Structured Outputs schema and return ``response.output_parsed``.

A conservative ``responses.create`` fallback is retained for older compatible
SDKs/endpoints.  The fallback uses :func:`strict_schema_from_pydantic`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from openai import OpenAI
from models import ExtractionProvenance, FAIRagroApplicationDataFitnessModel
from openai_schema import strict_schema_from_pydantic
from prompts import PromptBundle, build_prompt_bundle, build_repair_prompt_bundle
from settings import Settings, get_settings
from source_bundle import SourceBundle


@dataclass(frozen=True)
class LLMRunMetadata:
    generated_at_utc: str
    api: str
    requested_model: str
    response_model: Optional[str]
    response_id: Optional[str]
    system_prompt_version: str
    extraction_prompt_version: str
    system_prompt_sha256: str
    extraction_prompt_sha256: str
    schema_version: str
    source_bundle_sha256: str
    duration_seconds: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredExtraction:
    record: FAIRagroApplicationDataFitnessModel
    run_metadata: LLMRunMetadata


from review_utils import force_machine_review_status
from metric_postprocess import derive_fitness_metrics_from_canonical_ledger
from semantic_postprocess import postprocess_semantics
from evidence_postprocess import normalize_evidence_locators


class DFFPExtractor:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.validate_for_extraction()
        self.client = OpenAI(
            api_key=self.settings.openai_api_key,
            timeout=self.settings.timeout_seconds,
            max_retries=self.settings.max_retries,
        )

    def _build_prompts(self, bundle: SourceBundle) -> PromptBundle:
        return build_prompt_bundle(
            source_bundle_text=bundle.text_for_llm,
            source_name=bundle.source_filename,
            system_version=self.settings.system_prompt_version,
            extraction_version=self.settings.extraction_prompt_version,
        )

    def _base_kwargs(self, prompts: PromptBundle) -> dict[str, Any]:
        """Arguments shared by Responses parse/create calls.

        We use an explicit system + user input list because this form is shown in
        the Structured Outputs documentation and works naturally with parse().
        """

        kwargs: dict[str, Any] = {
            "model": self.settings.extraction_model,
            "input": [
                {"role": "system", "content": prompts.system_prompt},
                {"role": "user", "content": prompts.user_prompt},
            ],
            "max_output_tokens": self.settings.extraction_max_output_tokens,
        }
        if self.settings.extraction_reasoning_effort:
            kwargs["reasoning"] = {"effort": self.settings.extraction_reasoning_effort}
        return kwargs

    def _call_responses_parse(self, prompts: PromptBundle):
        """Preferred SDK-native Pydantic Structured Outputs path."""

        parse_method = getattr(self.client.responses, "parse", None)
        if not callable(parse_method):
            return None

        kwargs = self._base_kwargs(prompts)
        kwargs["text_format"] = FAIRagroApplicationDataFitnessModel
        return parse_method(**kwargs)

    def _call_responses_create_fallback(self, prompts: PromptBundle):
        """Fallback for older SDKs/endpoints without responses.parse()."""

        schema = strict_schema_from_pydantic(FAIRagroApplicationDataFitnessModel)
        text_format = {
            "type": "json_schema",
            "name": "fairagro_dffp_record",
            "description": (
                "Evidence-grounded FAIRagro Data-Fitness-for-Purpose metadata "
                "extracted from one scientific source bundle."
            ),
            "schema": schema,
            "strict": True,
        }
        kwargs = self._base_kwargs(prompts)
        kwargs["text"] = {"format": text_format}
        return self.client.responses.create(**kwargs)

    @staticmethod
    def _record_from_response(response) -> FAIRagroApplicationDataFitnessModel:
        """Return a validated record from either parse() or create()."""

        parsed = getattr(response, "output_parsed", None)
        if isinstance(parsed, FAIRagroApplicationDataFitnessModel):
            return parsed
        if parsed is not None:
            return FAIRagroApplicationDataFitnessModel.model_validate(parsed)

        output_text = getattr(response, "output_text", None)
        if not output_text:
            status = getattr(response, "status", None)
            incomplete = getattr(response, "incomplete_details", None)
            raise RuntimeError(
                "The Responses API returned neither output_parsed nor output_text. "
                f"status={status!r}, incomplete_details={incomplete!r}."
            )

        try:
            return FAIRagroApplicationDataFitnessModel.model_validate_json(output_text)
        except Exception as exc:
            preview = output_text[:3000]
            raise RuntimeError(
                "Structured response failed Pydantic validation: "
                f"{exc}\nResponse preview:\n{preview}"
            ) from exc

    def _finalize_record(self, record: FAIRagroApplicationDataFitnessModel, bundle: SourceBundle) -> FAIRagroApplicationDataFitnessModel:
        record = postprocess_semantics(record)
        record = normalize_evidence_locators(record, bundle.source_item_registry)
        force_machine_review_status(record)
        derive_fitness_metrics_from_canonical_ledger(record)

        # Deterministic provenance injection: these values come from our source package,
        # not from model interpretation.
        if record.document_metadata is not None:
            record.document_metadata.source = bundle.source_filename
        if record.extraction_provenance is None:
            record.extraction_provenance = ExtractionProvenance()
        record.extraction_provenance.source_fidelity_status = bundle.source_fidelity_status
        record.extraction_provenance.source_fidelity_initial_status = bundle.source_fidelity_initial_status
        record.extraction_provenance.unresolved_source_items = [
            f"{x.get('item_type')}:{x.get('item_id')}:{','.join(x.get('reason_codes', []) or [])}"
            for x in bundle.unresolved_items
        ] or None
        return record

    def _execute_prompt_bundle(self, bundle: SourceBundle, prompts: PromptBundle) -> StructuredExtraction:
        started = time.perf_counter()
        response = self._call_responses_parse(prompts)
        api_mode = "responses.parse"
        if response is None:
            response = self._call_responses_create_fallback(prompts)
            api_mode = "responses.create"
        duration = round(time.perf_counter() - started, 3)

        record = self._finalize_record(self._record_from_response(response), bundle)
        usage = getattr(response, "usage", None)
        metadata = LLMRunMetadata(
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            api=api_mode,
            requested_model=self.settings.extraction_model,
            response_model=getattr(response, "model", None),
            response_id=getattr(response, "id", None),
            system_prompt_version=prompts.system_version,
            extraction_prompt_version=prompts.extraction_version,
            system_prompt_sha256=prompts.system_sha256,
            extraction_prompt_sha256=prompts.extraction_sha256,
            schema_version=self.settings.schema_version,
            source_bundle_sha256=bundle.bundle_sha256,
            duration_seconds=duration,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            total_tokens=getattr(usage, "total_tokens", None) if usage else None,
        )
        return StructuredExtraction(record=record, run_metadata=metadata)

    def extract(self, bundle: SourceBundle) -> StructuredExtraction:
        return self._execute_prompt_bundle(bundle, self._build_prompts(bundle))

    def repair(self, bundle: SourceBundle, record: FAIRagroApplicationDataFitnessModel, issues: list[Any]) -> StructuredExtraction:
        """Run one focused source-grounded repair of deterministic semantic errors.

        This is intentionally invoked by the pipeline only when ERROR-level deterministic
        checks remain.  The repair still returns the full strict Pydantic model and receives
        the original source bundle, so it cannot bypass schema validation.
        """
        issue_payload = [x.to_dict() if hasattr(x, "to_dict") else x for x in issues]
        prompts = build_repair_prompt_bundle(
            source_bundle_text=bundle.text_for_llm,
            current_record_json=record.model_dump_json(indent=2, exclude_none=True),
            semantic_issues_json=json.dumps(issue_payload, ensure_ascii=False, indent=2),
            system_version=self.settings.system_prompt_version,
            repair_version=self.settings.semantic_repair_prompt_version,
        )
        return self._execute_prompt_bundle(bundle, prompts)

