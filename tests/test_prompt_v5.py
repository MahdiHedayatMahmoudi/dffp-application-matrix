from pathlib import Path


def test_extraction_v5_requires_atomic_metrics_and_explicit_beneficiaries():
    text = (Path(__file__).resolve().parents[1] / "prompts" / "extraction_v5.txt").read_text(encoding="utf-8")
    assert "one ValidationMetricRecord per metric, target, and aggregation scope" in text
    assert "winter wheat; all crops" in text
    assert "scope=crop_phase" in text
    assert "pipeline derives them deterministically" in text
    assert "Do not infer beneficiary groups" in text
