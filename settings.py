"""Central configuration for the full FAIRagro / DFFP pipeline.

All model names, prompt versions, ingestion/recovery defaults and paths are
configured from environment variables. The Streamlit app and CLI do not
hard-code them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # Environment variables still work in minimal audit/test environments.
    def load_dotenv() -> bool:
        return False

load_dotenv()

# Release-locked prompt/schema versions.  Old .env files must not silently make a
# newer package execute an older extraction contract.  Advanced experiments can
# opt in explicitly with DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE=true.
RELEASE_SYSTEM_PROMPT_VERSION = "system_v2"
RELEASE_EXTRACTION_PROMPT_VERSION = "extraction_v12"
RELEASE_SCHEMA_VERSION = "fairagro-dffp-v3.5.2"
RELEASE_REPAIR_PROMPT_VERSION = "repair_v4"


class ConfigurationError(RuntimeError):
    pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    # Extraction LLM
    openai_api_key: str
    extraction_model: str
    extraction_reasoning_effort: Optional[str]
    extraction_max_output_tokens: int
    timeout_seconds: float
    max_retries: int

    # Prompt/schema versions
    system_prompt_version: str
    extraction_prompt_version: str
    schema_version: str

    # Source package / ingestion
    source_package_root: str
    ingestion_table_mode: str
    ingestion_formula_enrichment: bool
    ingestion_ocr_mode: str
    ingestion_render_dpi: int

    # Multimodal recovery
    recovery_enabled_by_default: bool
    recovery_api_url: str
    recovery_api_type: str
    recovery_model: str
    recovery_reasoning_effort: Optional[str]
    recovery_max_tokens: int
    recovery_priorities: tuple[str, ...]
    recovery_statuses: tuple[str, ...]

    # Bundle controls
    include_all_structured_tables: bool
    include_recovered_figures: bool
    include_formulas: bool
    max_structured_item_chars: int

    # Optional one-pass semantic repair after deterministic validation
    semantic_repair_enabled: bool = True
    semantic_repair_max_attempts: int = 1
    semantic_repair_prompt_version: str = RELEASE_REPAIR_PROMPT_VERSION
    release_config_override_allowed: bool = False
    run_purpose: str = "test"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            extraction_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
            extraction_reasoning_effort=_env_optional("OPENAI_REASONING_EFFORT"),
            extraction_max_output_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "30000")),
            timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "240")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
            system_prompt_version=(
                os.getenv("DFFP_SYSTEM_PROMPT_VERSION", RELEASE_SYSTEM_PROMPT_VERSION).strip()
                if _env_bool("DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE", False) else RELEASE_SYSTEM_PROMPT_VERSION
            ),
            extraction_prompt_version=(
                os.getenv("DFFP_EXTRACTION_PROMPT_VERSION", RELEASE_EXTRACTION_PROMPT_VERSION).strip()
                if _env_bool("DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE", False) else RELEASE_EXTRACTION_PROMPT_VERSION
            ),
            schema_version=(
                os.getenv("DFFP_SCHEMA_VERSION", RELEASE_SCHEMA_VERSION).strip()
                if _env_bool("DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE", False) else RELEASE_SCHEMA_VERSION
            ),
            source_package_root=os.getenv("DFFP_SOURCE_PACKAGE_ROOT", "scientific_source_packages").strip(),
            ingestion_table_mode=os.getenv("DFFP_TABLE_MODE", "accurate").strip(),
            ingestion_formula_enrichment=_env_bool("DFFP_FORMULA_ENRICHMENT", True),
            ingestion_ocr_mode=os.getenv("DFFP_OCR_MODE", "auto").strip(),
            ingestion_render_dpi=int(os.getenv("DFFP_RECOVERY_RENDER_DPI", "300")),
            recovery_enabled_by_default=_env_bool("DFFP_RUN_RECOVERY", True),
            recovery_api_url=os.getenv("FAIRAGRO_VISION_API_URL", "https://api.openai.com/v1/responses").strip(),
            recovery_api_type=os.getenv("FAIRAGRO_VISION_API_TYPE", "responses").strip(),
            recovery_model=os.getenv("FAIRAGRO_VISION_MODEL", "gpt-5.6-luna").strip(),
            recovery_reasoning_effort=_env_optional("FAIRAGRO_VISION_REASONING_EFFORT"),
            recovery_max_tokens=int(os.getenv("FAIRAGRO_VISION_MAX_TOKENS", "12000")),
            recovery_priorities=tuple(
                x.strip() for x in os.getenv("DFFP_RECOVERY_PRIORITIES", "high,medium").split(",") if x.strip()
            ),
            recovery_statuses=tuple(
                x.strip() for x in os.getenv("DFFP_RECOVERY_STATUSES", "required,recommended,review").split(",") if x.strip()
            ),
            include_all_structured_tables=_env_bool("DFFP_INCLUDE_ALL_TABLES", True),
            include_recovered_figures=_env_bool("DFFP_INCLUDE_RECOVERED_FIGURES", True),
            include_formulas=_env_bool("DFFP_INCLUDE_FORMULAS", True),
            max_structured_item_chars=int(os.getenv("DFFP_MAX_STRUCTURED_ITEM_CHARS", "30000")),
            semantic_repair_enabled=_env_bool("DFFP_SEMANTIC_REPAIR", True),
            semantic_repair_max_attempts=max(0, int(os.getenv("DFFP_SEMANTIC_REPAIR_MAX_ATTEMPTS", "1"))),
            semantic_repair_prompt_version=(
                os.getenv("DFFP_SEMANTIC_REPAIR_PROMPT_VERSION", RELEASE_REPAIR_PROMPT_VERSION).strip()
                if _env_bool("DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE", False) else RELEASE_REPAIR_PROMPT_VERSION
            ),
            release_config_override_allowed=_env_bool("DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE", False),
            run_purpose=os.getenv("DFFP_RUN_PURPOSE", "test").strip().lower(),
        )

    def validate_for_extraction(self) -> None:
        if not self.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is missing. Add it to .env or your environment.")
        if not self.extraction_model:
            raise ConfigurationError("OPENAI_MODEL must not be empty.")
        if self.run_purpose not in {"test", "evaluation", "publication"}:
            raise ConfigurationError("DFFP_RUN_PURPOSE must be test, evaluation, or publication.")

    @property
    def ignored_release_config_overrides(self) -> dict[str, str]:
        """Return stale version pins ignored by the release lock."""
        if self.release_config_override_allowed:
            return {}
        expected = {
            "DFFP_SYSTEM_PROMPT_VERSION": RELEASE_SYSTEM_PROMPT_VERSION,
            "DFFP_EXTRACTION_PROMPT_VERSION": RELEASE_EXTRACTION_PROMPT_VERSION,
            "DFFP_SCHEMA_VERSION": RELEASE_SCHEMA_VERSION,
            "DFFP_SEMANTIC_REPAIR_PROMPT_VERSION": RELEASE_REPAIR_PROMPT_VERSION,
        }
        return {k: v for k, expected_value in expected.items() if (v := os.getenv(k)) and v.strip() != expected_value}

    @property
    def source_package_path(self) -> Path:
        return Path(self.source_package_root).expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
