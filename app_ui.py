"""Streamlit front end for the full provenance-aware DFFP pipeline."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import streamlit as st

from exporters import export_results
from pipeline import FullDFFPPipeline
from settings import ConfigurationError, get_settings
from ui_components import render_fitness_snapshot, render_methods_and_evidence, render_risk_and_caveats

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

st.set_page_config(page_title="FAIRagro DFFP Evidence Pipeline", layout="wide")
st.title("📊 FAIRagro DFFP Evidence Pipeline")
st.caption("Canonical PDF → Docling source package → fidelity audit → optional visual recovery → structured DFFP evidence")

settings = get_settings()
with st.sidebar:
    st.subheader("Configuration")
    st.code(
        f"Extraction model: {settings.extraction_model}\n"
        f"Vision model: {settings.recovery_model}\n"
        f"System prompt: {settings.system_prompt_version}\n"
        f"Extraction prompt: {settings.extraction_prompt_version}\n"
        f"Schema: {settings.schema_version}\n"
        f"Packages: {settings.source_package_root}"
    )
    run_recovery = st.checkbox(
        "Run queued multimodal recovery",
        value=settings.recovery_enabled_by_default,
        help="Recovers queued high/medium tables, figures and formulas before DFFP extraction. API usage may increase.",
    )
    overwrite = st.checkbox("Rebuild existing source package", value=False)

uploaded = st.file_uploader("Upload the canonical scientific PDF", type=["pdf"])

if uploaded is not None and st.button("🚀 Run full pipeline"):
    tmp_path = None
    tmp_dir = None
    try:
        # Preserve the uploaded filename in the canonical-source provenance rather
        # than letting a random temporary filename become the source name.
        tmp_dir = tempfile.mkdtemp(prefix="fairagro_upload_")
        tmp_path = str(Path(tmp_dir) / Path(uploaded.name).name)
        Path(tmp_path).write_bytes(uploaded.getvalue())

        pipe = FullDFFPPipeline(settings)
        # Fail fast on stale .env prompt versions or missing API configuration before
        # spending minutes on Docling conversion.
        pipe.preflight_extraction()
        with st.status("Running pipeline…", expanded=True) as status:
            st.write("1. Building Docling source package and source-fidelity audit…")
            report = pipe.ingest(tmp_path, overwrite=overwrite)
            package_dir = report["document"]["package_dir"]
            st.write(f"Source fidelity: **{report['summary']['overall_status']}**")
            st.write(f"Package: `{package_dir}`")

            if run_recovery:
                st.write("2. Running queued structured multimodal recovery…")
                recovery = pipe.recover(package_dir)
                st.write(
                    f"Recovery success={recovery.get('success_count', 0)}, failures={recovery.get('failure_count', 0)}"
                )
            else:
                st.write("2. Visual recovery skipped; unresolved items will be carried into provenance.")

            st.write("3. Building provenance-aware source bundle and extracting DFFP metadata…")
            result = pipe.extract_package(package_dir)
            semantic_errors = [x for x in result.validation_issues if x.severity == "error"]
            json_filename = "application_matrix.unvalidated.json" if semantic_errors else "application_matrix.json"
            exports = export_results([result.record], result.manifest, json_filename=json_filename)
            if semantic_errors:
                status.update(label="Pipeline completed with semantic errors", state="error")
            else:
                status.update(label="Pipeline completed", state="complete")

        st.session_state["result"] = result
        st.session_state["exports"] = exports

    except ConfigurationError as exc:
        st.error(f"Configuration error: {exc}")
    except Exception as exc:
        logging.exception("Pipeline failed")
        st.error(f"Pipeline failed: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        if tmp_dir and os.path.isdir(tmp_dir):
            os.rmdir(tmp_dir)

if "result" in st.session_state:
    result = st.session_state["result"]
    exports = st.session_state["exports"]

    st.subheader("🔎 Source fidelity / semantic checks")
    c1, c2, c3 = st.columns(3)
    c1.metric("Effective source fidelity", result.source_bundle.source_fidelity_status)
    c2.metric("Unresolved source items", len(result.source_bundle.unresolved_items))
    c3.metric("Bundle characters", len(result.source_bundle.text_for_llm))
    st.caption(f"Initial ingestion audit: {result.source_bundle.source_fidelity_initial_status}")

    for issue in result.validation_issues:
        text = f"{issue.code}: {issue.message}"
        if issue.severity == "error":
            st.error(text)
        elif issue.severity == "warning":
            st.warning(text)
        else:
            st.info(text)

    st.divider()
    render_fitness_snapshot(result.record)
    st.divider()
    render_risk_and_caveats(result.record)
    st.divider()
    render_methods_and_evidence(result.record)

    with st.expander("Technical extraction manifest", expanded=False):
        st.json(result.manifest)

    st.subheader("⬇️ Exports")
    specs = [
        ("DFFP JSON", exports.json_path, "application/json", Path(exports.json_path).name),
        ("Extraction manifest", exports.manifest_path, "application/json", "extraction_manifest.json"),
        ("Validation report", exports.validation_report_path, "application/json", "validation_report.json"),
        ("Excel", exports.excel_path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application_matrix.xlsx"),
        ("Interactive HTML", exports.interactive_html_path, "text/html", "application_matrix_interactive.html"),
    ]
    cols = st.columns(2)
    for i, (label, path, mime, filename) in enumerate(specs):
        with cols[i % 2]:
            with open(path, "rb") as fh:
                st.download_button(label, fh.read(), file_name=filename, mime=mime, use_container_width=True)
