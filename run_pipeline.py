"""Command-line entry point for the full FAIRagro / DFFP pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from exporters import export_results
from pipeline import FullDFFPPipeline


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="PDF/source-package -> provenance-aware FAIRagro DFFP extraction"
    )
    p.add_argument("source", help="Scientific PDF or an existing scientific source-package directory")
    p.add_argument("--no-recovery", action="store_true", help="Skip queued multimodal recovery before extraction")
    p.add_argument("--overwrite-package", action="store_true", help="Rebuild an existing source package")
    p.add_argument("--output-dir", default=None, help="Directory for application_matrix.json and reports")
    p.add_argument("--ingest-only", action="store_true", help="Build/audit the source package and stop")
    p.add_argument("--recover-only", action="store_true", help="Run recovery on an existing package and stop")
    p.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail with exit code 2 when deterministic semantic errors remain. "
            "The debug artifact is written as application_matrix.unvalidated.json instead of application_matrix.json."
        ),
    )
    return p.parse_args()


def main() -> None:
    a = parse_args()
    source = Path(a.source).expanduser().resolve()
    pipe = FullDFFPPipeline()
    ignored = pipe.settings.ignored_release_config_overrides
    if ignored:
        details = ", ".join(f"{k}={v!r}" for k, v in sorted(ignored.items()))
        print(
            "Release configuration lock: ignoring stale prompt/schema override(s): " + details
            + ". Set DFFP_ALLOW_RELEASE_CONFIG_OVERRIDE=true only for an intentional experiment."
        )

    # Validate extraction configuration before any expensive PDF conversion.
    # Ingest-only/recover-only workflows intentionally do not require extraction prompts.
    if not a.ingest_only and not a.recover_only:
        pipe.preflight_extraction()

    if source.is_dir():
        if a.recover_only:
            manifest = pipe.recover(source)
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return
        if a.ingest_only:
            raise SystemExit("--ingest-only requires a PDF input, not an existing package directory")
        result = pipe.extract_package(source)
    else:
        if source.suffix.lower() != ".pdf":
            raise SystemExit("Full provenance-aware ingestion currently expects a PDF or an existing source-package directory.")
        report = pipe.ingest(source, overwrite=a.overwrite_package)
        package_dir = Path(report["document"]["package_dir"])
        print(f"Source package: {package_dir}")
        print(f"Fidelity status: {report['summary']['overall_status']}")
        if a.ingest_only:
            return
        if not a.no_recovery:
            recovery = pipe.recover(package_dir)
            print(f"Recovered: {recovery.get('success_count')} | failures: {recovery.get('failure_count')}")
        if a.recover_only:
            return
        result = pipe.extract_package(package_dir)

    errors = [x for x in result.validation_issues if x.severity == "error"]
    warnings = [x for x in result.validation_issues if x.severity == "warning"]
    strict_failed = bool(a.strict and errors)
    json_filename = "application_matrix.unvalidated.json" if strict_failed else "application_matrix.json"
    exports = export_results(
        [result.record],
        result.manifest,
        output_dir=a.output_dir,
        json_filename=json_filename,
    )
    print(f"DFFP JSON: {exports.json_path}")
    print(f"Manifest: {exports.manifest_path}")
    print(f"Validation report: {exports.validation_report_path}")
    repair_runs = result.manifest.get("semantic_repair_runs") or []
    if repair_runs:
        print(f"Semantic repair runs: {len(repair_runs)}")
        consolidations = result.manifest.get("semantic_repair_consolidations") or []
        if consolidations:
            last = consolidations[-1]
            print(
                "Repair consolidation: "
                f"{last.get('preserved_initial_metrics', 0)} initial metric(s) preserved, "
                f"{last.get('structured_candidates_backfilled', 0)} structured sibling(s) backfilled, "
                f"{last.get('composite_metrics_demoted', 0)} composite summary(s) demoted, "
                f"{last.get('non_outcome_metrics_demoted', 0)} non-outcome metric definition(s) demoted, "
                f"{last.get('cross_target_evidence_removed', 0)} cross-target evidence record(s) removed, "
                f"{last.get('evidence_records_deduplicated', 0)} evidence duplicate(s) removed, "
                f"{last.get('tuning_parameters_moved', 0)} tuning parameter(s) separated"
            )
    if result.manifest.get("semantic_repair_failure"):
        print(f"Semantic repair failure: {result.manifest['semantic_repair_failure']}")
    print(f"Semantic checks: {len(errors)} error(s), {len(warnings)} warning(s)")
    if strict_failed:
        print("Strict validation failed: no validated application_matrix.json was emitted.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
