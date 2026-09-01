from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_workflow import (
    ReviewWorkflowError,
    derive_reviewed_matrix,
    initialize_review_workspace,
    publication_gate,
    publish_reviewed_matrix,
    record_review_decision,
    review_status,
    set_source_publication_state,
)


def _evidence(claim="Metric is 1.0"):
    return {
        "claim": claim,
        "evidence_type": "explicit",
        "source_section": "Results",
        "source_location": "Table 1",
        "source_text": claim,
        "source_page": 3,
        "source_item_id": "tbl_001",
        "source_modality": "author_table",
        "representation_method": "docling_structured",
        "review_status": "machine_extracted",
    }


def _matrix():
    ev = _evidence()
    return [{
        "document_metadata": {
            "title": "Synthetic study",
            "related_resources": [{
                "name": "Synthetic dataset",
                "identifier": "https://example.org/dataset",
                "resource_type": "dataset",
                "relation": "describes",
                "evidence": [_evidence("Dataset identifier is reported")],
            }],
        },
        "validation_and_diagnostics": {
            "validation_metrics": [{
                "name": "NSE",
                "value_or_summary": "0.82",
                "context": "out_of_sample_validation",
                "scope": "study_or_site",
                "scope_label": "Site A",
                "evaluation_support": "held_out_station",
                "evidence": [ev],
            }],
            "tuning_parameters": [{
                "name": "Threshold",
                "value_or_summary": "0.5",
                "scope": "model_or_method",
                "scope_label": "Method A",
                "evidence": [_evidence("Threshold is 0.5")],
            }],
            "evidence": [ev],  # duplicate claim should be reviewed once and updated everywhere
        },
        "outputs_and_fitness_indicators": {
            "fitness_for_use_metrics": {
                "producer_side_metrics": [{
                    "name": "NSE",
                    "value_or_summary": "0.82",
                    "context": "out_of_sample_validation",
                    "scope": "study_or_site",
                    "scope_label": "Site A",
                    "evaluation_support": "held_out_station",
                    "evidence": [ev],
                }]
            }
        },
        "extraction_provenance": {
            "review_status": "machine_extracted"
        },
    }]


def _write_baseline(tmp_path: Path):
    matrix = tmp_path / "application_matrix.json"
    manifest = tmp_path / "extraction_manifest.json"
    report = tmp_path / "validation_report.json"
    matrix.write_text(json.dumps(_matrix(), indent=2), encoding="utf-8")
    manifest.write_text(json.dumps({"release_config": {"schema_version": "fairagro-dffp-v3.2.8"}}), encoding="utf-8")
    report.write_text(json.dumps([{"severity": "info", "code": "NO_DETERMINISTIC_ISSUES"}]), encoding="utf-8")
    return matrix, manifest, report


def test_init_creates_stable_queue_and_immutable_baseline(tmp_path):
    matrix, manifest, report = _write_baseline(tmp_path)
    review_dir = tmp_path / "review"
    paths = initialize_review_workspace(matrix, review_dir=review_dir, extraction_manifest_path=manifest, validation_report_path=report)
    q1 = json.loads(paths.queue.read_text())
    assert q1["summary"]["required_remaining"] > 0
    ids1 = [x["review_id"] for x in q1["items"]]
    # Duplicate evidence in canonical metric + section is one review item.
    metric_evidence = [x for x in q1["items"] if x["kind"] == "evidence" and x["snapshot"]["claim"] == "Metric is 1.0"]
    assert len(metric_evidence) == 1

    # Reinitialization from the same bytes keeps IDs stable.
    initialize_review_workspace(matrix, review_dir=review_dir, extraction_manifest_path=manifest, validation_report_path=report)
    q2 = json.loads(paths.queue.read_text())
    assert ids1 == [x["review_id"] for x in q2["items"]]

    other = tmp_path / "other.json"
    changed = _matrix()
    changed[0]["document_metadata"]["title"] = "Changed"
    other.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ReviewWorkflowError):
        initialize_review_workspace(other, review_dir=review_dir)


def test_verify_evidence_updates_all_duplicate_copies_and_mirror_is_derived(tmp_path):
    matrix, manifest, report = _write_baseline(tmp_path)
    review_dir = tmp_path / "review"
    paths = initialize_review_workspace(matrix, review_dir=review_dir, extraction_manifest_path=manifest, validation_report_path=report)
    queue = json.loads(paths.queue.read_text())
    item = next(x for x in queue["items"] if x["kind"] == "evidence" and x["snapshot"]["claim"] == "Metric is 1.0")
    record_review_decision(review_dir, review_id=item["review_id"], action="verify", reviewer="A. Reviewer")
    out, _ = derive_reviewed_matrix(review_dir)
    payload = json.loads(out.read_text())
    record = payload[0]
    assert record["validation_and_diagnostics"]["validation_metrics"][0]["evidence"][0]["review_status"] == "human_verified"
    assert record["validation_and_diagnostics"]["evidence"][0]["review_status"] == "human_verified"
    # Deterministic fitness mirror is rebuilt from the reviewed canonical metric.
    assert record["outputs_and_fitness_indicators"]["fitness_for_use_metrics"]["producer_side_metrics"][0]["evidence"][0]["review_status"] == "human_verified"


def test_parent_rejection_makes_child_evidence_not_applicable(tmp_path):
    matrix, manifest, report = _write_baseline(tmp_path)
    review_dir = tmp_path / "review"
    paths = initialize_review_workspace(matrix, review_dir=review_dir, extraction_manifest_path=manifest, validation_report_path=report)
    queue = json.loads(paths.queue.read_text())
    resource = next(x for x in queue["items"] if x["kind"] == "related_resource")
    record_review_decision(review_dir, review_id=resource["review_id"], action="reject", reviewer="Reviewer", reason="Unsupported during review")
    review_status(review_dir)
    queue2 = json.loads(paths.queue.read_text())
    nested = [x for x in queue2["items"] if x.get("parent_review_id") == resource["review_id"]]
    assert nested and all(x["review_status"] == "not_applicable" for x in nested)
    out, _ = derive_reviewed_matrix(review_dir)
    payload = json.loads(out.read_text())
    assert not payload[0]["document_metadata"].get("related_resources")


def test_correction_keeps_evidence_separate_and_validates(tmp_path):
    matrix, manifest, report = _write_baseline(tmp_path)
    review_dir = tmp_path / "review"
    paths = initialize_review_workspace(matrix, review_dir=review_dir, extraction_manifest_path=manifest, validation_report_path=report)
    queue = json.loads(paths.queue.read_text())
    metric = next(x for x in queue["items"] if x["kind"] == "validation_metric")
    record_review_decision(
        review_dir,
        review_id=metric["review_id"],
        action="correct",
        reviewer="Reviewer",
        reason="Corrected against source",
        patch={"value_or_summary": "0.84"},
    )
    out, _ = derive_reviewed_matrix(review_dir)
    payload = json.loads(out.read_text())
    assert payload[0]["validation_and_diagnostics"]["validation_metrics"][0]["value_or_summary"] == "0.84"

    with pytest.raises(ReviewWorkflowError):
        record_review_decision(
            review_dir,
            review_id=metric["review_id"],
            action="correct",
            reviewer="Reviewer",
            reason="Attempt invalid evidence replacement",
            patch={"evidence": []},
        )
        derive_reviewed_matrix(review_dir)


def test_publication_gate_requires_review_freeze_and_identifier(tmp_path):
    matrix, manifest, report = _write_baseline(tmp_path)
    review_dir = tmp_path / "review"
    paths = initialize_review_workspace(matrix, review_dir=review_dir, extraction_manifest_path=manifest, validation_report_path=report)
    queue = json.loads(paths.queue.read_text())
    for item in queue["items"]:
        # Child evidence of a rejected parent is not used here; verify everything.
        record_review_decision(review_dir, review_id=item["review_id"], action="verify", reviewer="Reviewer")
    derive_reviewed_matrix(review_dir)
    gate = publication_gate(review_dir)
    assert not gate["ready_for_publication"]
    assert {x["id"] for x in gate["checks"] if not x["passed"]} == {
        "manuscript_frozen", "source_identifier_checked", "source_document_doi_resolved"
    }

    set_source_publication_state(
        review_dir,
        manuscript_frozen=True,
        source_document_doi="10.1234/final.paper",
        identifier_checked=True,
    )
    gate = publication_gate(review_dir)
    assert gate["ready_for_publication"]
    target = tmp_path / "FINAL_DFFP_application_matrix.json"
    publish_reviewed_matrix(review_dir, output_path=target)
    published = json.loads(target.read_text())
    assert published[0]["document_metadata"]["doi"] == "10.1234/final.paper"
    assert published[0]["extraction_provenance"]["review_status"] == "human_verified"
