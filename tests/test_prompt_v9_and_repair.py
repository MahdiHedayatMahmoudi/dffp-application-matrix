from prompts import build_prompt_bundle, build_repair_prompt_bundle, validate_repair_prompt_configuration


def test_prompt_v9_mentions_high_priority_and_workflow_performed_analysis_types():
    pb = build_prompt_bundle(
        source_bundle_text="SOURCE", source_name="paper.pdf",
        system_version="system_v2", extraction_version="extraction_v9",
    )
    text = pb.user_prompt.lower()
    assert "high-priority quantitative completeness candidates" in text
    assert "analysis type must be workflow-performed" in text
    assert "downstream result preservation" in text


def test_repair_prompt_contains_current_record_issues_and_source():
    validate_repair_prompt_configuration(repair_version="repair_v1")
    pb = build_repair_prompt_bundle(
        source_bundle_text="SOURCE_BUNDLE_X",
        current_record_json='{"x": 1}',
        semantic_issues_json='[{"code":"X"}]',
        system_version="system_v2", repair_version="repair_v1",
    )
    assert "SOURCE_BUNDLE_X" in pb.user_prompt
    assert '"code":"X"' in pb.user_prompt
    assert '"x": 1' in pb.user_prompt
