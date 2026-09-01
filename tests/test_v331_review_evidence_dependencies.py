from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_workflow import (
    ReviewWorkflowError,
    derive_reviewed_matrix,
    initialize_review_workspace,
    record_review_decision,
    review_conflicts,
)


def _ev(claim: str):
    return {
        "claim": claim,
        "evidence_type": "explicit",
        "source_section": "Results",
        "source_location": "Table 1",
        "source_text": claim,
        "source_page": 1,
        "source_item_id": "tbl_001",
        "source_modality": "author_table",
        "representation_method": "docling_structured",
        "review_status": "machine_extracted",
    }


def _record(evidence):
    return [{
        "document_metadata": {"title": "Synthetic study"},
        "validation_and_diagnostics": {
            "validation_metrics": [{
                "name": "MAE",
                "value_or_summary": "1.0",
                "unit": "days",
                "context": "out_of_sample_validation",
                "scope": "dataset_family",
                "scope_label": "All groups",
                "evaluation_support": "held_out_station",
                "evidence": evidence,
            }]
        },
        "extraction_provenance": {"review_status": "machine_extracted"},
    }]


def _workspace(tmp_path: Path, evidence):
    matrix = tmp_path / "application_matrix.json"
    manifest = tmp_path / "extraction_manifest.json"
    report = tmp_path / "validation_report.json"
    matrix.write_text(json.dumps(_record(evidence), indent=2), encoding="utf-8")
    manifest.write_text(json.dumps({
        "release_config": {
            "schema_version": "fairagro-dffp-v3.2.8",
            "extraction_prompt_version": "extraction_v9",
        }
    }), encoding="utf-8")
    report.write_text(json.dumps([{"severity": "info", "code": "NO_DETERMINISTIC_ISSUES"}]), encoding="utf-8")
    review_dir = tmp_path / "review"
    paths = initialize_review_workspace(
        matrix,
        review_dir=review_dir,
        extraction_manifest_path=manifest,
        validation_report_path=report,
    )
    return review_dir, paths


def test_rejecting_only_parent_evidence_is_blocked_before_save(tmp_path):
    review_dir, paths = _workspace(tmp_path, [_ev("MAE is 1.0 days")])
    queue = json.loads(paths.queue.read_text(encoding="utf-8"))
    evidence = next(x for x in queue["items"] if x["kind"] == "evidence")

    with pytest.raises(ReviewWorkflowError, match="only source evidence"):
        record_review_decision(
            review_dir,
            review_id=evidence["review_id"],
            action="reject",
            reviewer="Reviewer",
            reason="Evidence unsupported",
        )

    manifest = json.loads(paths.review_manifest.read_text(encoding="utf-8"))
    assert evidence["review_id"] not in manifest.get("decision_history", {})
    assert review_conflicts(review_dir) == []


def test_existing_v330_conflict_is_reported_actionably(tmp_path):
    review_dir, paths = _workspace(tmp_path, [_ev("MAE is 1.0 days")])
    queue = json.loads(paths.queue.read_text(encoding="utf-8"))
    evidence = next(x for x in queue["items"] if x["kind"] == "evidence")

    # Simulate a decision already written by v3.3.0 before the dependency guard existed.
    manifest = json.loads(paths.review_manifest.read_text(encoding="utf-8"))
    manifest.setdefault("decision_history", {})[evidence["review_id"]] = [{
        "action": "reject",
        "reviewer": "Reviewer",
        "reviewed_at_utc": "2026-09-01T00:00:00+00:00",
        "reason": "test",
        "source_checked": True,
        "patch": None,
        "baseline_snapshot_sha256": "synthetic",
    }]
    paths.review_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    conflicts = review_conflicts(review_dir)
    assert len(conflicts) == 1
    assert conflicts[0]["code"] == "REJECTED_LAST_PARENT_EVIDENCE"
    with pytest.raises(ReviewWorkflowError, match="Review decision conflict"):
        derive_reviewed_matrix(review_dir)


def test_rejecting_one_of_multiple_parent_evidence_records_remains_valid(tmp_path):
    review_dir, paths = _workspace(tmp_path, [_ev("MAE is 1.0 days"), _ev("MAE is reported in Table 1")])
    queue = json.loads(paths.queue.read_text(encoding="utf-8"))
    evidence = next(x for x in queue["items"] if x["kind"] == "evidence" and x["snapshot"]["claim"] == "MAE is 1.0 days")

    record_review_decision(
        review_dir,
        review_id=evidence["review_id"],
        action="reject",
        reviewer="Reviewer",
        reason="Evidence unsupported",
    )
    out, _ = derive_reviewed_matrix(review_dir)
    payload = json.loads(out.read_text(encoding="utf-8"))
    remaining = payload[0]["validation_and_diagnostics"]["validation_metrics"][0]["evidence"]
    assert len(remaining) == 1
    assert remaining[0]["claim"] == "MAE is reported in Table 1"
