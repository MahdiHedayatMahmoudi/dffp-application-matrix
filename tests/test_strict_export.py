from pathlib import Path
from exporters import export_results
from models import FAIRagroApplicationDataFitnessModel


def test_exporter_can_write_unvalidated_debug_artifact(tmp_path: Path):
    paths = export_results(
        [FAIRagroApplicationDataFitnessModel()],
        {"semantic_checks": [{"severity": "error", "code": "X"}]},
        output_dir=str(tmp_path),
        json_filename="application_matrix.unvalidated.json",
    )
    assert Path(paths.json_path).name == "application_matrix.unvalidated.json"
    assert not (tmp_path / "application_matrix.json").exists()
