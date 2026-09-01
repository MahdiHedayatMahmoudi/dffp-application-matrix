from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_workflow import (
    REVIEW_SCHEMA_VERSION,
    ReviewWorkflowError,
    derive_reviewed_matrix,
    initialize_review_workspace,
    migrate_review_workspace,
    publication_gate,
    record_review_decision,
    review_audit_issues,
)


def _ev(claim: str):
    return {
        "claim": claim,
        "evidence_type": "explicit",
        "source_section": "Results",
        "source_location": "Table 1",
        "source_text": claim,
        "source_modality": "author_table",
        "representation_method": "docling_structured",
        "review_status": "machine_extracted",
    }


def _matrix():
    mae = {
        "name": "MAE", "value_or_summary": "1.0", "unit": "days",
        "context": "out_of_sample_validation", "scope": "dataset_family",
        "scope_label": "All groups", "evaluation_support": "held_out_station",
        "evidence": [_ev("MAE is 1.0 days")],
    }
    wi = {
        "name": "WI", "value_or_summary": "16.3 ± 2.3", "unit": "mm",
        "context": "downstream_use_case", "scope": "use_case",
        "scope_label": "Example use case", "evaluation_support": "use_case",
        "evidence": [_ev("WI is 16.3 ± 2.3 mm")],
    }
    return [{
        "document_metadata": {"title": "Synthetic study"},
        "validation_and_diagnostics": {"validation_metrics": [mae, wi]},
        "outputs_and_fitness_indicators": {
            "fitness_for_use_metrics": {
                "producer_side_metrics": [mae],
                "application_specific_metrics": [wi],
            }
        },
        "extraction_provenance": {"review_status": "machine_extracted"},
    }]


def _workspace(tmp_path: Path):
    matrix = tmp_path / "application_matrix.json"
    manifest = tmp_path / "extraction_manifest.json"
    report = tmp_path / "validation_report.json"
    matrix.write_text(json.dumps(_matrix(), indent=2), encoding="utf-8")
    manifest.write_text(json.dumps({"release_config": {
        "schema_version": "fairagro-dffp-v3.2.8",
        "extraction_prompt_version": "extraction_v9",
    }}), encoding="utf-8")
    report.write_text(json.dumps([{"severity": "info", "code": "NO_DETERMINISTIC_ISSUES"}]), encoding="utf-8")
    review_dir = tmp_path / "review"
    paths = initialize_review_workspace(matrix, review_dir=review_dir, extraction_manifest_path=manifest, validation_report_path=report)
    return review_dir, paths


def test_rejected_canonical_metrics_are_not_resurrected_from_fitness_mirrors(tmp_path):
    review_dir, paths = _workspace(tmp_path)
    queue = json.loads(paths.queue.read_text(encoding="utf-8"))
    metrics = [x for x in queue["items"] if x["kind"] == "validation_metric"]
    for item in metrics:
        record_review_decision(
            review_dir, review_id=item["review_id"], action="reject",
            reviewer="Reviewer", reason="Synthetic rejection regression test",
        )
    out, _ = derive_reviewed_matrix(review_dir)
    record = json.loads(out.read_text(encoding="utf-8"))[0]
    assert not record["validation_and_diagnostics"].get("validation_metrics")
    fitness = record["outputs_and_fitness_indicators"]["fitness_for_use_metrics"]
    assert not fitness.get("producer_side_metrics")
    assert not fitness.get("application_specific_metrics")


def test_reason_required_and_noop_correction_blocked(tmp_path):
    review_dir, paths = _workspace(tmp_path)
    queue = json.loads(paths.queue.read_text(encoding="utf-8"))
    metric = next(x for x in queue["items"] if x["kind"] == "validation_metric")
    with pytest.raises(ReviewWorkflowError, match="meaningful review reason"):
        record_review_decision(review_dir, review_id=metric["review_id"], action="reject", reviewer="Reviewer")
    with pytest.raises(ReviewWorkflowError, match="no-op"):
        record_review_decision(
            review_dir, review_id=metric["review_id"], action="correct",
            reviewer="Reviewer", reason="Testing no-op", patch={"value_or_summary": metric["snapshot"]["value_or_summary"]},
        )


def test_v1_workspace_migrates_preserving_history_and_invalidating_derived_artifact(tmp_path):
    review_dir, paths = _workspace(tmp_path)
    queue = json.loads(paths.queue.read_text(encoding="utf-8"))
    metric = next(x for x in queue["items"] if x["kind"] == "validation_metric")
    record_review_decision(review_dir, review_id=metric["review_id"], action="verify", reviewer="Reviewer")
    derive_reviewed_matrix(review_dir)
    assert paths.reviewed_matrix.exists()

    q = json.loads(paths.queue.read_text(encoding="utf-8")); q["review_schema_version"] = "fairagro-dffp-review-v1"
    m = json.loads(paths.review_manifest.read_text(encoding="utf-8")); m["review_schema_version"] = "fairagro-dffp-review-v1"
    paths.queue.write_text(json.dumps(q, indent=2), encoding="utf-8")
    paths.review_manifest.write_text(json.dumps(m, indent=2), encoding="utf-8")

    info = migrate_review_workspace(review_dir)
    assert info["review_schema_version"] == REVIEW_SCHEMA_VERSION
    assert not paths.reviewed_matrix.exists()
    migrated = json.loads(paths.review_manifest.read_text(encoding="utf-8"))
    assert migrated["decision_history"][metric["review_id"]][-1]["action"] == "verify"
    assert migrated["workspace_migrations"][-1]["to_version"] == REVIEW_SCHEMA_VERSION


def test_legacy_missing_reason_and_noop_correction_block_publication_quality_gate(tmp_path):
    review_dir, paths = _workspace(tmp_path)
    queue = json.loads(paths.queue.read_text(encoding="utf-8"))
    metric = next(x for x in queue["items"] if x["kind"] == "validation_metric")
    manifest = json.loads(paths.review_manifest.read_text(encoding="utf-8"))
    manifest["decision_history"][metric["review_id"]] = [{
        "action": "correct", "reviewer": "Legacy reviewer", "reviewed_at_utc": "2026-01-01T00:00:00+00:00",
        "reason": None, "source_checked": True,
        "patch": {"value_or_summary": metric["snapshot"]["value_or_summary"]},
        "baseline_snapshot_sha256": "legacy",
    }]
    paths.review_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    issues = review_audit_issues(review_dir)
    codes = {x["code"] for x in issues}
    assert "MISSING_REQUIRED_REASON" in codes
    assert "NO_OP_CORRECTION" in codes
    gate = publication_gate(review_dir)
    quality = next(x for x in gate["checks"] if x["id"] == "review_decision_quality_issues_zero")
    assert quality["passed"] is False
