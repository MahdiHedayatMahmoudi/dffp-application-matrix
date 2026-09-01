import json
from pathlib import Path

from settings import Settings
from source_bundle import build_source_bundle


def settings(tmp_path):
    return Settings(
        openai_api_key="x",
        extraction_model="test",
        extraction_reasoning_effort=None,
        extraction_max_output_tokens=1000,
        timeout_seconds=10,
        max_retries=0,
        system_prompt_version="system_v2",
        extraction_prompt_version="extraction_v2",
        schema_version="test",
        source_package_root=str(tmp_path),
        ingestion_table_mode="accurate",
        ingestion_formula_enrichment=True,
        ingestion_ocr_mode="auto",
        ingestion_render_dpi=300,
        recovery_enabled_by_default=False,
        recovery_api_url="https://example.test/responses",
        recovery_api_type="responses",
        recovery_model="test",
        recovery_reasoning_effort=None,
        recovery_max_tokens=1000,
        recovery_priorities=("high", "medium"),
        recovery_statuses=("required", "recommended", "review"),
        include_all_structured_tables=True,
        include_recovered_figures=True,
        include_formulas=True,
        max_structured_item_chars=10000,
    )


def test_bundle_uses_clean_markdown_and_structured_table(tmp_path: Path):
    pkg = tmp_path / "pkg"
    (pkg / "docling").mkdir(parents=True)
    (pkg / "audit").mkdir()
    (pkg / "tables" / "tbl_001").mkdir(parents=True)
    (pkg / "docling" / "document.clean.md").write_text("# Paper\nAuthored text", encoding="utf-8")
    (pkg / "tables" / "tbl_001" / "table.records.json").write_text(
        json.dumps([{"Requirement": "1 km"}]), encoding="utf-8"
    )
    manifest = {"core_exports": {"clean_markdown": "docling/document.clean.md"}}
    fidelity = {
        "document": {"filename": "paper.pdf", "sha256": "abc", "package_dir": str(pkg)},
        "summary": {"overall_status": "pass"},
        "recovery_queue": [],
        "tables": [{"table_id": "tbl_001", "folder": "tables/tbl_001", "page_no": 3, "caption": "Table 1"}],
        "formulas": [],
        "figures": [],
    }
    (pkg / "source_package_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pkg / "audit" / "source_fidelity.json").write_text(json.dumps(fidelity), encoding="utf-8")

    bundle = build_source_bundle(pkg, settings(tmp_path))
    assert "Authored text" in bundle.text_for_llm
    assert "AUTHOR_TABLE id=tbl_001 page=3" in bundle.text_for_llm
    assert '"Requirement": "1 km"' in bundle.text_for_llm
