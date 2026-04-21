# app_ui.py → Enhanced interactive Streamlit pipeline

import os
import json
import tempfile
import logging
import pandas as pd
import streamlit as st

from dotenv import load_dotenv
from openai import OpenAI
from typing import List
from pydantic import TypeAdapter

from models import FAIRagroApplicationDataFitnessModel
from utils import (
    extract_text_from_file,
    extract_text_from_pdf,
    format_list_field_as_html,
    save_dataframe_as_html,
    save_interactive_matrix_html,
)
from dffp_pipeline import DFFPPipeline


# --- Setup ---
st.set_page_config(page_title="DFFP Application Matrix", layout="wide")
st.title("📊 Data Fitness Explorer")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# =========================
# 🎛️ Helpers
# =========================
def format_list_as_text(val):
    """Convert list to plain bullet-like text for Streamlit display."""
    if isinstance(val, list):
        return "\n".join(str(item).strip() for item in val)
    return val if val is not None else ""


# =========================
# 🎛️ UI Rendering Helpers
# =========================
def render_badge(label: str, value: str, color: str):
    st.markdown(
        f"""
        <div style="
            display:inline-block;
            padding:6px 12px;
            margin-right:8px;
            border-radius:16px;
            background-color:{color};
            color:white;
            font-weight:600;
            font-size:0.9em;">
            {label}: {value}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_list(title: str, items):
    st.markdown(f"**{title}**")
    if items:
        for item in items:
            st.markdown(f"- {item}")
    else:
        st.markdown("_Not specified_")


def render_fitness_snapshot(use_case: FAIRagroApplicationDataFitnessModel):
    st.subheader("🎯 Fitness Snapshot")

    fc = use_case.fitness_classification
    em = use_case.evidence_and_maturity

    col1, col2 = st.columns(2)

    with col1:
        render_list("✅ Suitable for", fc.suitable_for if fc else None)
        render_list("❌ Not recommended for", fc.not_recommended_for if fc else None)

    with col2:
        if em:
            render_badge(
                "Validation strength",
                em.validation_strength.value if em.validation_strength else "unknown",
                "#2e7d32",
            )
            render_badge(
                "Transferability risk",
                em.transferability_risk.value if em.transferability_risk else "unknown",
                "#c62828",
            )
            render_badge(
                "Operational readiness",
                em.operational_readiness.value if em.operational_readiness else "unknown",
                "#455a64",
            )

            if em.evidence_notes:
                st.markdown("**Evidence notes**")
                st.info(em.evidence_notes)


def render_risk_and_caveats(use_case: FAIRagroApplicationDataFitnessModel):
    st.subheader("⚠️ Risks & Caveats")

    dr = use_case.decision_risk_profile
    lr = use_case.limitations_and_risks

    col1, col2 = st.columns(2)

    with col1:
        render_list("Failure modes", dr.failure_modes if dr else None)
        render_list("Consequences of misuse", dr.consequences_of_misuse if dr else None)

    with col2:
        render_list("Known limitations", lr.known_limitations if lr else None)

    if dr and dr.acceptable_uncertainty_levels:
        st.markdown("**Acceptable uncertainty levels**")
        st.warning(dr.acceptable_uncertainty_levels)


def render_methods_and_evidence(use_case: FAIRagroApplicationDataFitnessModel):
    with st.expander("🧪 Methods, Data & Evidence (details)", expanded=False):

        if use_case.application_profile:
            st.markdown("### Application profile")
            st.json(use_case.application_profile.model_dump(exclude_none=True))

        if use_case.analysis_characteristics:
            st.markdown("### Analysis characteristics")
            st.json(use_case.analysis_characteristics.model_dump(exclude_none=True))

        if use_case.input_data_requirements:
            st.markdown("### Input data requirements")
            st.json(use_case.input_data_requirements.model_dump(exclude_none=True))

        if use_case.processing_pipeline:
            st.markdown("### Processing pipeline")
            st.json(use_case.processing_pipeline.model_dump(exclude_none=True))

        if use_case.validation_and_diagnostics:
            st.markdown("### Validation & diagnostics")
            st.json(use_case.validation_and_diagnostics.model_dump(exclude_none=True))

        if use_case.reproducibility_and_fairness:
            st.markdown("### Reproducibility & FAIRness")
            st.json(use_case.reproducibility_and_fairness.model_dump(exclude_none=True))


# =========================
# 📄 File Upload
# =========================
uploaded_files = st.file_uploader(
    "📄 Upload multiple papers (PDF, TXT, MD)",
    type=["pdf", "txt", "md"],
    accept_multiple_files=True,
)


# =========================
# 🚀 Extraction Pipeline
# =========================
if uploaded_files:

    if st.button("🚀 Run Extraction & Analysis"):

        use_cases: List[FAIRagroApplicationDataFitnessModel] = []

        with st.spinner("Processing files... ⏳"):

            for uploaded_file in uploaded_files:

                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=os.path.splitext(uploaded_file.name)[1],
                ) as tmp_file:
                    tmp_file.write(uploaded_file.read())
                    tmp_path = tmp_file.name

                report_text = extract_text_from_file(tmp_path)

                prompt = f"""
Extract structured FAIRagro metadata:

{report_text}
"""

                response = client.chat.completions.parse(
                    model="gpt-5-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": "You extract structured scientific metadata.",
                        },
                        {"role": "user", "content": prompt.strip()},
                    ],
                    response_format=FAIRagroApplicationDataFitnessModel,
                )

                use_cases.append(response.choices[0].message.parsed)

        st.session_state["use_cases"] = use_cases
        st.success(f"✅ Extracted {len(use_cases)} papers")


# =========================
# 🧠 DFFP PIPELINE
# =========================
if "use_cases" in st.session_state:

    st.subheader("🧠 Cross-Paper Intelligence Layer")

    if st.button("🧠 Generate DFFP Analysis"):

        try:
            with st.spinner("Running DFFP analysis... 🧠"):
                pipeline = DFFPPipeline(client)
                dffp_result = pipeline.run(st.session_state["use_cases"])

                st.session_state["dffp"] = dffp_result
                st.success("✅ DFFP analysis completed!")

        except Exception as e:
            st.error(f"DFFP failed: {e}")


# =========================
# 📊 DISPLAY USE CASES
# =========================
if "use_cases" in st.session_state:

    for i, use_case in enumerate(st.session_state["use_cases"]):

        st.divider()
        st.subheader(f"📄 Paper {i + 1}")

        render_fitness_snapshot(use_case)
        st.divider()
        render_risk_and_caveats(use_case)
        st.divider()
        render_methods_and_evidence(use_case)


# =========================
# 📋 EXPORT SECTION
# =========================
if "use_cases" in st.session_state:

    st.divider()
    st.subheader("📋 Full Extracted Model & Exports")

    st.markdown(
        """
        This section provides full transparency of extracted FAIRagro models.
        """
    )

    df_raw = pd.DataFrame(
        [uc.model_dump(exclude_none=True) for uc in st.session_state["use_cases"]]
    )
    df_display = df_raw.map(format_list_as_text)

    # =========================
    # 📦 Prepare export data
    # =========================


    use_cases_data = st.session_state["use_cases"]

    use_cases_json = [uc.model_dump(exclude_none=True) for uc in use_cases_data]

    st.download_button(
    label="📥 Download All Extracted Papers (JSON)",
    data=json.dumps(use_cases_json, indent=2),
    file_name="fairagro_use_cases.json",
    mime="application/json",
    )
    
    csv_data = df_display.to_csv(index=False)

    st.download_button(
        label="📥 Download Table (CSV)",
        data=csv_data,
        file_name="fairagro_table.csv",
        mime="text/csv",
    )


    # =========================
    # 🌐 HTML export (table)
    # =========================

    st.subheader("🌐 HTML Export")

    if st.button("🌐 Generate HTML Table"):

        html_path = os.path.join(tempfile.gettempdir(), "fairagro_table.html")

        # Convert lists to HTML-friendly format
        df_html = df_raw.map(format_list_field_as_html)

        save_dataframe_as_html(
            df_html,
            html_path,
            title="FAIRagro Extracted Papers",
        )

        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # Store in session to persist after rerun
        st.session_state["fairagro_html"] = html_content

        st.success("✅ HTML report generated!")


    # Show download ONLY if already generated
    if "fairagro_html" in st.session_state:

        st.download_button(
            label="📥 Download Table (HTML)",
            data=st.session_state["fairagro_html"],
            file_name="fairagro_table.html",
            mime="text/html",
        )

    with st.expander("📊 View flattened table"):
        st.dataframe(df_display, use_container_width=True, height=400)


if "dffp" in st.session_state:

    dffp = st.session_state["dffp"]

    st.divider()
    st.header("🧠 Data Fitness-for-Purpose Analysis")

    # =========================
    # 🌐 HTML GENERATION
    # =========================

    st.subheader("🌐 HTML Report")

    # Prepare JSON once (cheap, no problem)
    dffp_json = {
        "dataset": [d.model_dump() for d in dffp.dataset],
        "categories": [c.model_dump() for c in dffp.categories],
        "matrix": [m.model_dump() for m in dffp.matrix],
        "narrative": dffp.narrative,
    }

    # Button to generate HTML
    if st.button("🌐 Generate DFFP HTML Report"):

        html_path = os.path.join(tempfile.gettempdir(), "dffp_report.html")

        html_path, _ = save_interactive_matrix_html(
            dffp_json,
            html_path,
            title="DFFP Analysis Report",
        )

        with open(html_path, "r", encoding="utf-8") as f:
            st.session_state["dffp_html"] = f.read()

        st.success("✅ DFFP HTML report generated!")

    # =========================
    # 📥 DOWNLOADS (GROUPED)
    # =========================
    st.subheader("📥 Downloads")

    st.download_button(
        "📥 Full JSON",
        dffp.model_dump_json(indent=2),
        "dffp_result.json",
        "application/json",
    )

    st.download_button(
        "📥 Dataset",
        json.dumps([d.model_dump() for d in dffp.dataset], indent=2),
        "dffp_dataset.json",
        "application/json",
    )

    st.download_button(
        "📥 Categories",
        json.dumps([c.model_dump() for c in dffp.categories], indent=2),
        "dffp_categories.json",
        "application/json",
    )

    st.download_button(
        "📥 Fitness Matrix",
        json.dumps([m.model_dump() for m in dffp.matrix], indent=2),
        "dffp_matrix.json",
        "application/json",
    )

    st.download_button(
        "📥 Narrative",
        dffp.narrative or "",
        "dffp_narrative.txt",
        "text/plain",
    )



    # Show download ONLY if HTML was generated
    if "dffp_html" in st.session_state:

        st.download_button(
            "🌐 Interactive HTML Report",
            st.session_state["dffp_html"],
            "dffp_interactive_report.html",
            "text/html",
        )
    # =========================
    # 📊 DISPLAY
    # =========================
    st.subheader("📦 Dataset View")
    st.json([d.model_dump() for d in dffp.dataset])

    st.subheader("📊 Categories")
    st.json([c.model_dump() for c in dffp.categories])

    st.subheader("📈 Fitness Matrix")
    st.json([m.model_dump() for m in dffp.matrix])

    st.subheader("📖 Narrative")
    st.write(dffp.narrative)