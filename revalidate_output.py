"""Revalidate an existing DFFP extraction without another LLM/API call."""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from evidence_postprocess import normalize_evidence_locators
from exporters import export_results
from metric_postprocess import derive_fitness_metrics_from_canonical_ledger
from models import FAIRagroApplicationDataFitnessModel
from semantic_checks import CheckIssue, validate_record
from semantic_postprocess import postprocess_semantics
from repair_consolidation import enrich_structured_metric_semantics
from settings import RELEASE_SCHEMA_VERSION
from source_bundle import SourceBundle


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_bundle_from_manifest(manifest: dict[str, Any]) -> SourceBundle:
    payload = dict(manifest.get("source_package") or {})
    if not payload:
        raise ValueError("Manifest has no source_package metadata; deterministic source checks cannot be reproduced.")
    allowed = {item.name for item in fields(SourceBundle)}
    payload = {key: value for key, value in payload.items() if key in allowed}
    payload["text_for_llm"] = ""  # hashes and candidate registries are already recorded in the manifest
    return SourceBundle(**payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Revalidate and export an existing DFFP matrix without calling the LLM"
    )
    parser.add_argument("matrix", help="Existing application_matrix(.unvalidated).json")
    parser.add_argument("--manifest", required=True, help="Matching extraction_manifest.json")
    parser.add_argument("--output-dir", required=True, help="New directory for revalidated exports")
    parser.add_argument("--strict", action="store_true", help="Exit 2 and keep an unvalidated filename if errors remain")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    matrix_path = Path(args.matrix).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = _load_json(manifest_path)
    payload = _load_json(matrix_path)
    if not isinstance(payload, list) or not payload:
        raise SystemExit("Matrix must be a non-empty JSON array of DFFP records.")

    bundle = _source_bundle_from_manifest(manifest)
    records: list[FAIRagroApplicationDataFitnessModel] = []
    issues: list[CheckIssue] = []
    structured_semantics_enriched_total = 0
    for item in payload:
        record = FAIRagroApplicationDataFitnessModel.model_validate(item)
        record = postprocess_semantics(record)
        structured_semantics_enriched = enrich_structured_metric_semantics(record, bundle)
        structured_semantics_enriched_total += structured_semantics_enriched
        record = normalize_evidence_locators(record, bundle.source_item_registry)
        derive_fitness_metrics_from_canonical_ledger(record)
        records.append(record)
        issues.extend(validate_record(record, bundle))

    # A clean record contributes its informational success marker. Keep just one.
    non_success = [issue for issue in issues if issue.code != "NO_DETERMINISTIC_ISSUES"]
    issues = non_success or [CheckIssue(
        "info", "NO_DETERMINISTIC_ISSUES", "No configured deterministic semantic checks failed."
    )]
    errors = [issue for issue in issues if issue.severity == "error"]
    publication_run = any(
        record.extraction_provenance is not None
        and record.extraction_provenance.run_purpose.value == "publication"
        for record in records
    )
    strict_failed = bool((args.strict or publication_run) and errors)

    updated_manifest = dict(manifest)
    updated_manifest["semantic_checks"] = [issue.to_dict() for issue in issues]
    updated_manifest["revalidation_run"] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validator_schema_version": RELEASE_SCHEMA_VERSION,
        "source_matrix_sha256": _sha256_file(matrix_path),
        "source_manifest_sha256": _sha256_file(manifest_path),
        "llm_api_called": False,
        "structured_semantics_enriched": structured_semantics_enriched_total,
    }
    filename = "application_matrix.unvalidated.json" if strict_failed else "application_matrix.json"
    exports = export_results(
        records,
        updated_manifest,
        output_dir=args.output_dir,
        json_filename=filename,
    )
    print(f"DFFP JSON: {exports.json_path}")
    print(f"Manifest: {exports.manifest_path}")
    print(f"Validation report: {exports.validation_report_path}")
    print(f"JSON Schema: {exports.schema_path}")
    if exports.ro_crate_metadata_path:
        print(f"RO-Crate metadata: {exports.ro_crate_metadata_path}")
    print(f"Semantic checks: {len(errors)} error(s), {sum(x.severity == 'warning' for x in issues)} warning(s)")
    print("LLM API called: no")
    if strict_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
