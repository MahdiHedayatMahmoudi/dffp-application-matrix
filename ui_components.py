"""Reusable Streamlit rendering helpers."""

from __future__ import annotations

import streamlit as st

from models import FAIRagroApplicationDataFitnessModel


def _enum_value(value, default: str = "unknown") -> str:
    if value is None:
        return default
    return getattr(value, "value", str(value))


def render_list(title: str, items) -> None:
    st.markdown(f"**{title}**")
    if items:
        for item in items:
            st.markdown(f"- {item}")
    else:
        st.markdown("_Not specified_")


def render_fitness_snapshot(record: FAIRagroApplicationDataFitnessModel) -> None:
    st.subheader("🎯 Fitness snapshot")
    fc = record.fitness_classification
    em = record.evidence_and_maturity
    c1, c2 = st.columns(2)
    with c1:
        render_list("Suitable for", fc.suitable_for if fc else None)
        render_list("Conditionally suitable for", fc.conditionally_suitable_for if fc else None)
        render_list("Not recommended for", fc.not_recommended_for if fc else None)
        if fc:
            st.caption(f"Assessment basis: {_enum_value(fc.assessment_basis, 'unassessed')}")
    with c2:
        if em:
            st.write({
                "validation_strength": _enum_value(em.validation_strength),
                "operational_readiness": _enum_value(em.operational_readiness),
                "transferability_risk": _enum_value(em.transferability_risk),
                "assessment_basis": _enum_value(em.assessment_basis, "unassessed"),
            })
            if em.evidence_notes:
                st.info(em.evidence_notes)
        else:
            st.info("No decision-oriented maturity assessment extracted.")


def render_risk_and_caveats(record: FAIRagroApplicationDataFitnessModel) -> None:
    st.subheader("⚠️ Risks and caveats")
    dr = record.decision_risk_profile
    lr = record.limitations_and_risks
    c1, c2 = st.columns(2)
    with c1:
        render_list("Failure modes", dr.failure_modes if dr else None)
        render_list("Consequences of misuse", dr.consequences_of_misuse if dr else None)
    with c2:
        render_list("Known limitations", lr.known_limitations if lr else None)
        render_list("Extrapolation limits", lr.extrapolation_limits if lr else None)
        if dr and dr.acceptable_uncertainty_levels:
            st.warning(dr.acceptable_uncertainty_levels)


def render_methods_and_evidence(record: FAIRagroApplicationDataFitnessModel) -> None:
    with st.expander("🧪 Methods, data and evidence", expanded=False):
        sections = [
            ("Document metadata", record.document_metadata),
            ("Dataset characteristics", record.dataset_characteristics),
            ("Application profile", record.application_profile),
            ("Analysis characteristics", record.analysis_characteristics),
            ("Downstream application requirements", record.input_data_requirements),
            ("Producer workflow inputs", record.producer_workflow_inputs),
            ("Processing pipeline", record.processing_pipeline),
            ("Data quality and uncertainty", record.data_quality_dependencies),
            ("Validation and diagnostics", record.validation_and_diagnostics),
            ("Outputs and fitness evidence", record.outputs_and_fitness_indicators),
            ("Limitations and risks", record.limitations_and_risks),
            ("Reproducibility and FAIRness", record.reproducibility_and_fairness),
            ("Scalability and operations", record.scalability_and_operational_aspects),
            ("Extraction provenance", record.extraction_provenance),
        ]
        for title, value in sections:
            if value is not None:
                st.markdown(f"### {title}")
                st.json(value.model_dump(mode="json", by_alias=True, exclude_none=True))
