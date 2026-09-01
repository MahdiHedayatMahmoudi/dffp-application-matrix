import json
from pathlib import Path

from source_bundle import build_source_bundle


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _make_package(tmp_path: Path, *, overall_status: str, queue: list[dict]) -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "docling").mkdir(parents=True)
    (pkg / "docling" / "document.clean.md").write_text("# Test\nAuthored prose.", encoding="utf-8")
    _write_json(
        pkg / "source_package_manifest.json",
        {"core_exports": {"clean_markdown": "docling/document.clean.md"}},
    )
    _write_json(
        pkg / "audit" / "source_fidelity.json",
        {
            "document": {"filename": "paper.pdf", "sha256": "abc"},
            "summary": {"overall_status": overall_status},
            "recovery_queue": queue,
            "tables": [],
            "figures": [],
            "formulas": [],
        },
    )
    return pkg


def test_effective_status_pass_after_successful_recovery(tmp_path):
    queue = [{"item_type": "figure", "item_id": "fig_001", "status": "recommended"}]
    pkg = _make_package(tmp_path, overall_status="multimodal_review_recommended", queue=queue)
    _write_json(pkg / "figures" / "fig_001" / "figure.recovered.json", {"ok": True})

    bundle = build_source_bundle(pkg)

    assert bundle.source_fidelity_initial_status == "multimodal_review_recommended"
    assert bundle.source_fidelity_status == "pass_after_recovery"
    assert bundle.unresolved_items == []


def test_effective_status_stays_initial_while_items_unresolved(tmp_path):
    queue = [{"item_type": "figure", "item_id": "fig_001", "status": "recommended"}]
    pkg = _make_package(tmp_path, overall_status="multimodal_review_recommended", queue=queue)

    bundle = build_source_bundle(pkg)

    assert bundle.source_fidelity_initial_status == "multimodal_review_recommended"
    assert bundle.source_fidelity_status == "multimodal_review_recommended"
    assert len(bundle.unresolved_items) == 1


def test_effective_status_remains_pass_when_no_recovery_was_needed(tmp_path):
    pkg = _make_package(tmp_path, overall_status="pass", queue=[])

    bundle = build_source_bundle(pkg)

    assert bundle.source_fidelity_initial_status == "pass"
    assert bundle.source_fidelity_status == "pass"
    assert bundle.unresolved_items == []
