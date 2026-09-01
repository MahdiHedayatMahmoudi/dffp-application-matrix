from pathlib import Path


def test_v8_prompt_is_cross_document_and_generic():
    text = Path('prompts/extraction_v8.txt').read_text(encoding='utf-8')
    assert 'CROSS-DOCUMENT / DOMAIN-AGNOSTIC CONTRACT' in text
    assert 'Paper-specific' in text or 'paper-specific' in text
    assert 'source_item_id only when the evidence itself is the structured' in text
    assert 'scope=variable_or_layer' in text
    assert 'current source bundle supports' in text
