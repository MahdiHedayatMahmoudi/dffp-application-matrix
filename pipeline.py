"""End-to-end orchestration: PDF -> source package -> optional recovery -> DFFP record."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from llm_service import DFFPExtractor
from models import FAIRagroApplicationDataFitnessModel
from multimodal_recovery import EndpointConfig, RecoveryConfig, run_recovery
from prompts import PromptError, validate_prompt_configuration, validate_repair_prompt_configuration
from semantic_checks import CheckIssue, validate_record
from settings import ConfigurationError, Settings, get_settings
from source_bundle import SourceBundle, build_source_bundle, save_source_bundle
from repair_consolidation import consolidate_repair_record
from metric_postprocess import derive_fitness_metrics_from_canonical_ledger
from semantic_postprocess import postprocess_semantics
from evidence_postprocess import normalize_evidence_locators


@dataclass(frozen=True)
class PipelineResult:
    record: FAIRagroApplicationDataFitnessModel
    manifest: dict[str, Any]
    validation_issues: list[CheckIssue]
    source_bundle: SourceBundle
    package_dir: str


class FullDFFPPipeline:
    def __init__(self, settings: Settings | None = None, extractor: DFFPExtractor | None = None):
        self.settings = settings or get_settings()
        # Lazy extractor creation allows ingestion/audit-only workflows without an API key.
        self.extractor = extractor

    def preflight_extraction(self) -> None:
        """Validate API/model and prompt configuration before expensive ingestion."""
        self.settings.validate_for_extraction()
        try:
            validate_prompt_configuration(
                system_version=self.settings.system_prompt_version,
                extraction_version=self.settings.extraction_prompt_version,
            )
            if self.settings.semantic_repair_enabled and self.settings.semantic_repair_max_attempts > 0:
                validate_repair_prompt_configuration(repair_version=self.settings.semantic_repair_prompt_version)
        except PromptError as exc:
            raise ConfigurationError(str(exc)) from exc

    def ingest(self, pdf_path: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
        # Lazy import keeps extraction-from-existing-package usable even in a lightweight
        # environment where Docling is not installed. Full PDF ingestion still requires it.
        from scientific_pdf_ingestion import IngestionConfig, ConverterPool, ingest_pdf

        cfg = IngestionConfig(
            input_dir=str(Path(pdf_path).parent),
            output_root=str(self.settings.source_package_path),
            table_mode=self.settings.ingestion_table_mode,
            do_formula_enrichment=self.settings.ingestion_formula_enrichment,
            ocr_mode=self.settings.ingestion_ocr_mode,
            recovery_render_dpi=self.settings.ingestion_render_dpi,
            enable_picture_descriptions=False,
            overwrite_existing_package=overwrite,
        )
        pool = ConverterPool(cfg)
        return ingest_pdf(Path(pdf_path), cfg, pool=pool)

    def recover(self, package_dir: str | Path) -> dict[str, Any]:
        endpoint = EndpointConfig(
            api_url=self.settings.recovery_api_url,
            model=self.settings.recovery_model,
            api_type=self.settings.recovery_api_type,
            api_key_env="OPENAI_API_KEY",
            max_tokens=self.settings.recovery_max_tokens,
            reasoning_effort=self.settings.recovery_reasoning_effort,
            image_detail="high",
        )
        cfg = RecoveryConfig(
            package_dir=str(package_dir),
            endpoint=endpoint,
            statuses=self.settings.recovery_statuses,
            priorities=self.settings.recovery_priorities,
            item_types=("table", "figure", "formula"),
            write_enriched_markdown=True,
        )
        return run_recovery(cfg)

    def extract_package(self, package_dir: str | Path) -> PipelineResult:
        bundle = build_source_bundle(package_dir, self.settings)
        bundle_path = save_source_bundle(bundle)
        extractor = self.extractor or DFFPExtractor(self.settings)
        extraction = extractor.extract(bundle)
        initial_metadata = extraction.run_metadata
        issues = validate_record(extraction.record, bundle)

        repair_runs: list[dict[str, Any]] = []
        repair_consolidations: list[dict[str, Any]] = []
        repair_failure: str | None = None
        initial_record = extraction.record.model_copy(deep=True)
        repair_method = getattr(extractor, "repair", None)
        if (
            self.settings.semantic_repair_enabled
            and self.settings.semantic_repair_max_attempts > 0
            and callable(repair_method)
        ):
            for attempt in range(self.settings.semantic_repair_max_attempts):
                errors = [x for x in issues if x.severity == "error"]
                if not errors:
                    break
                try:
                    repaired = repair_method(bundle, extraction.record, errors)
                except Exception as exc:  # keep the first extraction available for strict/non-strict diagnostics
                    repair_failure = f"{type(exc).__name__}: {exc}"
                    break
                consolidated_record, consolidation_report = consolidate_repair_record(initial_record, repaired.record, bundle)
                consolidated_record = postprocess_semantics(consolidated_record)
                consolidated_record = normalize_evidence_locators(consolidated_record, bundle.source_item_registry)
                derive_fitness_metrics_from_canonical_ledger(consolidated_record)
                extraction = type(repaired)(record=consolidated_record, run_metadata=repaired.run_metadata)
                repair_runs.append(repaired.run_metadata.to_dict())
                repair_consolidations.append(consolidation_report.to_dict())
                issues = validate_record(extraction.record, bundle)

        manifest = {
            "release_config": {
                "system_prompt_version": self.settings.system_prompt_version,
                "extraction_prompt_version": self.settings.extraction_prompt_version,
                "schema_version": self.settings.schema_version,
                "repair_prompt_version": self.settings.semantic_repair_prompt_version,
                "override_allowed": self.settings.release_config_override_allowed,
                "ignored_stale_environment_overrides": self.settings.ignored_release_config_overrides,
            },
            "source_package": bundle.manifest_dict(),
            "source_bundle_path": str(bundle_path),
            "extraction_run": initial_metadata.to_dict(),
            "semantic_repair_runs": repair_runs,
            "semantic_repair_consolidations": repair_consolidations,
            "semantic_repair_failure": repair_failure,
            "final_structured_run": extraction.run_metadata.to_dict(),
            "semantic_checks": [x.to_dict() for x in issues],
        }
        return PipelineResult(
            record=extraction.record,
            manifest=manifest,
            validation_issues=issues,
            source_bundle=bundle,
            package_dir=str(Path(package_dir).resolve()),
        )

    def run_pdf(
        self,
        pdf_path: str | Path,
        *,
        run_recovery_first: Optional[bool] = None,
        overwrite_package: bool = False,
    ) -> PipelineResult:
        self.preflight_extraction()
        report = self.ingest(pdf_path, overwrite=overwrite_package)
        package_dir = report["document"]["package_dir"]

        do_recovery = self.settings.recovery_enabled_by_default if run_recovery_first is None else run_recovery_first
        if do_recovery:
            self.recover(package_dir)

        return self.extract_package(package_dir)
