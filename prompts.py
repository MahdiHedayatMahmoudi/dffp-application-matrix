"""Versioned prompt loading with reproducibility hashes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str
    system_version: str
    extraction_version: str
    system_sha256: str
    extraction_sha256: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def available_prompt_versions() -> list[str]:
    """Return available prompt template stems, e.g. system_v2/extraction_v2."""
    if not PROMPT_DIR.exists():
        return []
    return sorted(x.stem for x in PROMPT_DIR.glob("*.txt") if x.is_file())


def _load(version: str) -> str:
    path = PROMPT_DIR / f"{version}.txt"
    if not path.exists():
        available = ", ".join(available_prompt_versions()) or "[none]"
        raise PromptError(
            f"Prompt not found: {path}. Available prompt versions: {available}. "
            "Check DFFP_SYSTEM_PROMPT_VERSION and DFFP_EXTRACTION_PROMPT_VERSION in .env."
        )
    return path.read_text(encoding="utf-8").strip()


def validate_prompt_configuration(*, system_version: str, extraction_version: str) -> None:
    """Fail fast before expensive PDF ingestion when .env points to stale prompts."""
    _load(system_version)
    template = _load(extraction_version)
    if "{{SOURCE_BUNDLE}}" not in template:
        raise PromptError(f"{extraction_version}.txt must contain {{SOURCE_BUNDLE}}")


def build_prompt_bundle(*, source_bundle_text: str, source_name: str, system_version: str, extraction_version: str) -> PromptBundle:
    system = _load(system_version)
    template = _load(extraction_version)
    if "{{SOURCE_BUNDLE}}" not in template:
        raise PromptError(f"{extraction_version}.txt must contain {{SOURCE_BUNDLE}}")
    user = template.replace("{{SOURCE_NAME}}", source_name).replace("{{SOURCE_BUNDLE}}", source_bundle_text)
    return PromptBundle(
        system_prompt=system,
        user_prompt=user,
        system_version=system_version,
        extraction_version=extraction_version,
        system_sha256=_sha256(system),
        extraction_sha256=_sha256(template),
    )


def validate_repair_prompt_configuration(*, repair_version: str) -> None:
    template = _load(repair_version)
    required = ("{{SOURCE_BUNDLE}}", "{{CURRENT_RECORD}}", "{{SEMANTIC_ISSUES}}")
    missing = [x for x in required if x not in template]
    if missing:
        raise PromptError(f"{repair_version}.txt is missing required placeholder(s): {', '.join(missing)}")


def build_repair_prompt_bundle(*, source_bundle_text: str, current_record_json: str, semantic_issues_json: str,
                               system_version: str, repair_version: str) -> PromptBundle:
    system = _load(system_version)
    template = _load(repair_version)
    required = ("{{SOURCE_BUNDLE}}", "{{CURRENT_RECORD}}", "{{SEMANTIC_ISSUES}}")
    missing = [x for x in required if x not in template]
    if missing:
        raise PromptError(f"{repair_version}.txt is missing required placeholder(s): {', '.join(missing)}")
    user = (template
            .replace("{{CURRENT_RECORD}}", current_record_json)
            .replace("{{SEMANTIC_ISSUES}}", semantic_issues_json)
            .replace("{{SOURCE_BUNDLE}}", source_bundle_text))
    return PromptBundle(
        system_prompt=system,
        user_prompt=user,
        system_version=system_version,
        extraction_version=repair_version,
        system_sha256=_sha256(system),
        extraction_sha256=_sha256(template),
    )
