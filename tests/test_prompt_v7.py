from pathlib import Path


def test_prompt_v7_has_completeness_and_registry_rules():
    text = Path('prompts/extraction_v7.txt').read_text(encoding='utf-8')
    assert 'SOURCE ITEM REGISTRY / LOCATORS' in text
    assert 'QUANTITATIVE COMPLETENESS CHECKLIST' in text
    assert 'Demonstrated downstream numerical results' in text
    assert 'PICP and MPIW are both represented' in text
