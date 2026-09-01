"""CLI for the FAIRagro DFFP human-review/publication workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from review_workflow import (
    ReviewWorkflowError,
    derive_reviewed_matrix,
    initialize_review_workspace,
    migrate_review_workspace,
    publication_gate,
    publish_reviewed_matrix,
    record_review_decision,
    review_audit_issues,
    review_conflicts,
    review_status,
    set_source_publication_state,
)


def _json_arg(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Human review and publication workflow for a strict-pass DFFP matrix")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create an immutable review workspace from a machine-generated matrix")
    init.add_argument("matrix")
    init.add_argument("--review-dir", required=True)
    init.add_argument("--manifest", default=None, help="Machine extraction_manifest.json")
    init.add_argument("--validation-report", default=None, help="Machine validation_report.json")
    init.add_argument("--reviewer", default=None)

    decide = sub.add_parser("decide", help="Record verify/correct/reject/defer for one stable review ID")
    decide.add_argument("review_dir")
    decide.add_argument("review_id")
    decide.add_argument("action", choices=["verify", "correct", "reject", "defer"])
    decide.add_argument("--reviewer", default=None)
    decide.add_argument("--reason", default=None)
    decide.add_argument("--patch-file", default=None, help="JSON object patch required for 'correct'")
    decide.add_argument("--source-not-checked", action="store_true")

    status = sub.add_parser("status", help="Show review progress")
    status.add_argument("review_dir")

    migrate = sub.add_parser("migrate", help="Migrate an older v3.3 review workspace to the current review schema")
    migrate.add_argument("review_dir")

    audit = sub.add_parser("audit", help="Show provenance-quality issues in latest review decisions")
    audit.add_argument("review_dir")

    conflicts = sub.add_parser("conflicts", help="Show contradictory review decisions that block derivation")
    conflicts.add_argument("review_dir")

    derive = sub.add_parser("derive", help="Derive application_matrix.reviewed.json from baseline + decisions")
    derive.add_argument("review_dir")

    source = sub.add_parser("source-state", help="Record manuscript freeze and source-document identifier state")
    source.add_argument("review_dir")
    frozen = source.add_mutually_exclusive_group()
    frozen.add_argument("--frozen", action="store_true")
    frozen.add_argument("--not-frozen", action="store_true")
    checked = source.add_mutually_exclusive_group()
    checked.add_argument("--identifier-checked", action="store_true")
    checked.add_argument("--identifier-not-checked", action="store_true")
    source.add_argument("--source-doi", default=None)

    gate = sub.add_parser("gate", help="Run the deterministic publication gate")
    gate.add_argument("review_dir")
    gate.add_argument("--allow-missing-source-doi", action="store_true")

    publish = sub.add_parser("publish", help="Publish reviewed matrix only if the gate passes")
    publish.add_argument("review_dir")
    publish.add_argument("--output", required=True, help="Explicit stable publication filename/path")
    publish.add_argument("--allow-missing-source-doi", action="store_true")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    try:
        if a.command == "init":
            paths = initialize_review_workspace(
                a.matrix,
                review_dir=a.review_dir,
                extraction_manifest_path=a.manifest,
                validation_report_path=a.validation_report,
                reviewer=a.reviewer,
            )
            print(f"Review queue: {paths.queue}")
            print(f"Review manifest: {paths.review_manifest}")
            print(json.dumps(review_status(paths.review_dir), ensure_ascii=False, indent=2))
        elif a.command == "decide":
            event = record_review_decision(
                a.review_dir,
                review_id=a.review_id,
                action=a.action,
                reviewer=a.reviewer,
                reason=a.reason,
                patch=_json_arg(a.patch_file),
                source_checked=not a.source_not_checked,
            )
            print(json.dumps(event, ensure_ascii=False, indent=2))
            print(json.dumps(review_status(a.review_dir), ensure_ascii=False, indent=2))
        elif a.command == "status":
            print(json.dumps(review_status(a.review_dir), ensure_ascii=False, indent=2))
        elif a.command == "migrate":
            print(json.dumps(migrate_review_workspace(a.review_dir), ensure_ascii=False, indent=2))
        elif a.command == "audit":
            issues = review_audit_issues(a.review_dir)
            print(json.dumps(issues, ensure_ascii=False, indent=2))
            if issues:
                raise SystemExit(5)
        elif a.command == "conflicts":
            conflicts = review_conflicts(a.review_dir)
            print(json.dumps(conflicts, ensure_ascii=False, indent=2))
            if conflicts:
                raise SystemExit(4)
        elif a.command == "derive":
            path, info = derive_reviewed_matrix(a.review_dir)
            print(f"Reviewed matrix: {path}")
            print(json.dumps(info, ensure_ascii=False, indent=2))
        elif a.command == "source-state":
            frozen = True if a.frozen else False if a.not_frozen else None
            checked = True if a.identifier_checked else False if a.identifier_not_checked else None
            state = set_source_publication_state(
                a.review_dir,
                manuscript_frozen=frozen,
                source_document_doi=a.source_doi,
                identifier_checked=checked,
            )
            print(json.dumps(state, ensure_ascii=False, indent=2))
        elif a.command == "gate":
            gate = publication_gate(a.review_dir, require_source_doi=not a.allow_missing_source_doi)
            print(json.dumps(gate, ensure_ascii=False, indent=2))
            if not gate["ready_for_publication"]:
                raise SystemExit(3)
        elif a.command == "publish":
            path = publish_reviewed_matrix(
                a.review_dir,
                output_path=a.output,
                require_source_doi=not a.allow_missing_source_doi,
            )
            print(f"Published matrix: {path}")
    except ReviewWorkflowError as exc:
        raise SystemExit(f"Review workflow error: {exc}")


if __name__ == "__main__":
    main()
