"""Export DFFP records and technical provenance independently of Streamlit."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from models import FAIRagroApplicationDataFitnessModel


@dataclass(frozen=True)
class ExportPaths:
    output_dir: str
    json_path: str
    manifest_path: str
    validation_report_path: str
    excel_path: str
    html_path: str
    interactive_html_path: str


def record_to_dict(record: FAIRagroApplicationDataFitnessModel) -> dict[str, Any]:
    return record.model_dump(mode="json", by_alias=True, exclude_none=True)


def _format(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return str(value)


def _to_dataframe(records: list[FAIRagroApplicationDataFitnessModel]) -> pd.DataFrame:
    df = pd.DataFrame([record_to_dict(r) for r in records])
    return df.map(_format)


def _write_static_html(df: pd.DataFrame, path: Path, title: str) -> None:
    table = df.to_html(index=False, escape=True)
    path.write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;vertical-align:top;white-space:pre-wrap}}th{{font-weight:700}}</style>
</head><body><h1>{title}</h1><div class="wrap">{table}</div></body></html>""",
        encoding="utf-8",
    )


def _write_interactive_html(payload: dict[str, Any], path: Path) -> None:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>FAIRagro DFFP Explorer</title>
<style>body{font-family:Arial,sans-serif;margin:24px;max-width:1300px}details{border:1px solid #ddd;border-radius:8px;margin:8px 0;padding:10px}summary{cursor:pointer;font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head>
<body><h1>FAIRagro DFFP Explorer</h1><div id="root"></div><script id="data" type="application/json">__DATA__</script>
<script>const data=JSON.parse(document.getElementById('data').textContent);const root=document.getElementById('root');function add(t,v){const d=document.createElement('details');const s=document.createElement('summary');s.textContent=t;const p=document.createElement('pre');p.textContent=JSON.stringify(v,null,2);d.appendChild(s);d.appendChild(p);root.appendChild(d);}Object.entries(data).forEach(([k,v])=>add(k,v));</script></body></html>""".replace("__DATA__", data)
    path.write_text(html, encoding="utf-8")


def export_results(
    records: list[FAIRagroApplicationDataFitnessModel],
    manifest: dict[str, Any],
    *,
    output_dir: str | None = None,
    json_filename: str = "application_matrix.json",
) -> ExportPaths:
    out = Path(output_dir or tempfile.mkdtemp(prefix="fairagro_dffp_"))
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / json_filename
    manifest_path = out / "extraction_manifest.json"
    validation_path = out / "validation_report.json"
    excel_path = out / "application_matrix.xlsx"
    html_path = out / "application_matrix.html"
    interactive_path = out / "application_matrix_interactive.html"

    record_dicts = [record_to_dict(r) for r in records]
    json_path.write_text(json.dumps(record_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path.write_text(
        json.dumps(manifest.get("semantic_checks", []), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    df = _to_dataframe(records)
    df.to_excel(excel_path, index=False)
    _write_static_html(df, html_path, "FAIRagro Application Matrix")
    _write_interactive_html({"records": record_dicts, "manifest": manifest}, interactive_path)

    return ExportPaths(
        output_dir=str(out),
        json_path=str(json_path),
        manifest_path=str(manifest_path),
        validation_report_path=str(validation_path),
        excel_path=str(excel_path),
        html_path=str(html_path),
        interactive_html_path=str(interactive_path),
    )
