from pathlib import Path


def test_extraction_v6_is_lossless_and_evidence_grounded():
    text = (Path(__file__).resolve().parents[1] / "prompts" / "extraction_v6.txt").read_text(encoding="utf-8")
    assert "ATOMIC SPLITTING MUST BE LOSSLESS" in text
    assert "both MAE and RMSE" in text
    assert "both PICP and MPIW" in text
    assert "scope=dataset_family is only" in text
    assert "Every ValidationMetricRecord must include at least one evidence record" in text
    assert "context=downstream_use_case" in text
    assert 'analysis_type="model calibration"' in text
