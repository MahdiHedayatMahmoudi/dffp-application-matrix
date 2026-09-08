"""Deterministic semantic normalization after LLM extraction.

Transformations here are deliberately document-agnostic. They normalize controlled
terminology, remove guarded analysis labels when the producer workflow does not
support their meaning, and propagate already-supported spatial applicability limits
without inventing document-specific geography, crops, values, or thresholds.
"""

from __future__ import annotations

import re
from typing import Any

from models import FAIRagroApplicationDataFitnessModel, MetricContext
from quantitative_values import enrich_quantitative_values
from metric_postprocess import deduplicate_canonical_metrics

_SOURCE_KEYS = {"source_text", "source_section", "source_location"}

_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bPIC prediction[- ]interval calibration statistics\b", re.I),
     "PIC prediction-interval coverage and width statistics"),
    (re.compile(r"\bPIC prediction[- ]interval calibration tables\b", re.I),
     "PIC prediction-interval coverage and width tables"),
    (re.compile(r"\bpredictive[- ]interval calibration\b", re.I),
     "prediction-interval assessment"),
    (re.compile(r"\bprediction[- ]interval calibration\b", re.I),
     "prediction-interval assessment"),
    (re.compile(r"\binterval-calibration\b", re.I),
     "interval-assessment"),
    (re.compile(r"\binterval calibration metrics\b", re.I),
     "interval-assessment metrics"),
    (re.compile(r"\binterval calibration statistics\b", re.I),
     "interval-assessment statistics"),
)


def _normalize_string(text: str) -> str:
    out = text
    for pattern, replacement in _REPLACEMENTS:
        out = pattern.sub(replacement, out)
    return out


def _normalize_payload(value: Any, key: str | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value if key in _SOURCE_KEYS else _normalize_string(value)
    if isinstance(value, list):
        return [_normalize_payload(x, key=None) for x in value]
    if isinstance(value, dict):
        return {k: _normalize_payload(v, key=k) for k, v in value.items()}
    return value


def normalize_interval_terminology(
    record: FAIRagroApplicationDataFitnessModel,
) -> FAIRagroApplicationDataFitnessModel:
    payload = record.model_dump(mode="json", by_alias=True, exclude_none=False)
    return FAIRagroApplicationDataFitnessModel.model_validate(_normalize_payload(payload))


def ensure_interval_support_limitation(record: FAIRagroApplicationDataFitnessModel) -> None:
    vad = record.validation_and_diagnostics
    if vad is None:
        return
    metrics = list(vad.validation_metrics or [])
    held_out_interval = any(
        getattr(m.context, "value", str(m.context)) == MetricContext.cross_validated_interval_assessment.value
        and getattr(m.evaluation_support, "value", str(m.evaluation_support)) == "held_out_station"
        for m in metrics
    )
    if not held_out_interval:
        return

    # Tailor the wording to the current document rather than assuming raster data.
    raster_text: list[str] = []
    dc = record.dataset_characteristics
    if dc is not None:
        raster_text.extend(dc.variables_or_layers or [])
        raster_text.extend(dc.formats or [])
        if dc.spatial is not None:
            raster_text.extend([dc.spatial.native_resolution or "", dc.spatial.support_or_unit or ""])
    raster_like = bool(re.search(r"\b(?:raster|grid|pixel|geotiff|cog)\b", " ".join(raster_text), re.I))
    sentence = (
        "Prediction-interval assessment is based on held-out observation locations, not all raster/grid cells."
        if raster_like else
        "Prediction-interval assessment is based on held-out observation locations and does not by itself establish performance at unobserved locations or supports."
    )
    limitations = list(vad.validation_limitations or [])
    normalized = " ".join(x.lower() for x in limitations)
    if not ("held-out observation" in normalized and ("not all" in normalized or "does not" in normalized)):
        limitations.append(sentence)
    vad.validation_limitations = limitations


_SPATIAL_APPLICABILITY_TERMS = re.compile(
    r"(?:occurrence|presence|absence|land[ -]?use|suitability|mask(?:ed|ing)?|"
    r"spatial support|field[- ]level|pixel[- ]level|extrapolat|outside|beyond|sampled .* range|training .* range)",
    re.I,
)


def ensure_supported_spatial_applicability_limitation(record: FAIRagroApplicationDataFitnessModel) -> None:
    """Copy an already-extracted spatial applicability limitation into the spatial block.

    No paper-specific statement is synthesized. The function reuses an existing
    limitation/recommendation sentence only when it clearly concerns spatial support,
    masking, occurrence/presence, suitability, or extrapolation.
    """
    dc = record.dataset_characteristics
    if dc is None or dc.spatial is None:
        return

    candidates: list[str] = []
    lr = record.limitations_and_risks
    if lr is not None:
        candidates.extend(lr.known_limitations or [])
        candidates.extend(lr.risk_of_misuse or [])
        candidates.extend(lr.extrapolation_limits or [])
    ap = record.application_profile
    if ap is not None:
        candidates.extend(ap.not_suitable_for or [])
        candidates.extend(ap.recommended_context or [])

    spatial_candidates = [x.strip() for x in candidates if x and _SPATIAL_APPLICABILITY_TERMS.search(x)]
    if not spatial_candidates:
        return

    limitations = list(dc.spatial.known_scale_limitations or [])
    existing = " ".join(limitations).lower()
    for sentence in spatial_candidates:
        tokens = {t for t in re.findall(r"[a-z0-9]+", sentence.lower()) if len(t) >= 5}
        if tokens and sum(1 for t in tokens if t in existing) / len(tokens) >= 0.55:
            continue
        limitations.append(sentence)
        break  # one concise propagated applicability limit is enough
    dc.spatial.known_scale_limitations = limitations


_METRIC_DESCRIPTOR_TERMS = re.compile(
    r"\b(?:metric|metrics|statistic|statistics|mean|median|range|error|accuracy|coverage|"
    r"interval|width|sharpness|precision|uncertainty|mae|rmse|mse|picp|mpiw|bse|score)\b", re.I
)

def normalize_dataset_family_scope_labels(record: FAIRagroApplicationDataFitnessModel) -> None:
    """Keep dataset-family scope_label limited to the scientific target.

    Labels such as ``Eight groups; per-group median RMSE range`` mix a target with a
    statistic description.  The latter already belongs in metric name/value/interpretation.
    This generic cleanup does not infer a domain label; it only removes clearly metric-like
    suffixes from an existing aggregate label.
    """
    vad = record.validation_and_diagnostics
    if vad is None:
        return
    for metric in vad.validation_metrics or []:
        scope = getattr(metric.scope, "value", str(metric.scope))
        if scope != "dataset_family" or not metric.scope_label:
            continue
        label = metric.scope_label.strip()
        # Semicolon/pipe suffixes are never part of a single target label.
        for sep in (";", " | "):
            if sep in label:
                head, tail = label.split(sep, 1)
                if _METRIC_DESCRIPTOR_TERMS.search(tail):
                    label = head.strip()
                    break
        # ``All groups and prediction intervals`` -> ``All groups`` when the suffix is
        # clearly a metric/assessment descriptor rather than another target.
        m = re.match(r"^(.+?)\s+and\s+(.+)$", label, re.I)
        if m and _METRIC_DESCRIPTOR_TERMS.search(m.group(2)):
            label = m.group(1).strip()
        metric.scope_label = label or metric.scope_label


_CALIBRATION_WORKFLOW = re.compile(
    r"\bcalibrat(?:e|ed|es|ing|ion)\b|parameter\s+(?:estimat|fit)|"
    r"fit(?:ting)?\s+(?:the\s+)?parameters|inverse\s+model(?:ing|ling)|"
    r"parameter\s+optim(?:isation|ization)\s+against\s+(?:observ|data)",
    re.I,
)

_REMOTE_SENSING_WORKFLOW = re.compile(
    r"\bremote[- ]sens(?:ing|ed)\b|\bsatellite\b|\baerial imagery\b|\bimagery\b|"
    r"\b(?:landsat|sentinel(?:-?1|-?2)?|modis|viirs|sar|lidar|hyperspectral|multispectral)\b|"
    r"\breflectance\b|\bspectral (?:band|index|feature)s?\b|\b(?:ndvi|evi|savi)\b",
    re.I,
)

_MACHINE_LEARNING_WORKFLOW = re.compile(
    r"\bmachine[- ]learning\b|\bdeep[- ]learning\b|\bneural networks?\b|"
    r"\b(?:cnn|rnn|lstm|autoencoder)s?\b|\b(?:vision )?transformer (?:model|network|architecture)s?\b|"
    r"\brandom forests?\b|\bgradient[- ]boost(?:ing|ed)\b|\bboosted (?:tree|regression tree)s?\b|"
    r"\b(?:xgboost|lightgbm|catboost)\b|\bsupport[- ]vector (?:machine|regression|classifier)s?\b|\bsvm\b|"
    r"\bk[- ]?nearest[- ]?neighbou?rs?\b|\bknn\b|\bdecision trees?\b|"
    r"\b(?:supervised|unsupervised|reinforcement) learning\b|"
    r"\b(?:scikit[- ]learn|tensorflow|pytorch|keras)\b",
    re.I,
)

# Some controlled analysis labels are especially prone to leaking from application-domain
# discussions into the producer analysis description.  Each guarded label is retained only
# when the producer workflow/input/method text contains generic evidence that the analysis was
# actually performed.  The rules contain no document-specific entities, values, sections or IDs.
_ANALYSIS_WORKFLOW_GUARDS: dict[str, re.Pattern[str]] = {
    "model calibration": _CALIBRATION_WORKFLOW,
    "remote-sensing analysis": _REMOTE_SENSING_WORKFLOW,
    "machine learning": _MACHINE_LEARNING_WORKFLOW,
}


def _producer_workflow_text(record: FAIRagroApplicationDataFitnessModel) -> str:
    chunks: list[str] = []
    ac = record.analysis_characteristics
    if ac is not None:
        chunks.extend(ac.modeling_approach or [])
    pp = record.processing_pipeline
    if pp is not None:
        chunks.extend(pp.preprocessing_steps or [])
        chunks.extend(pp.feature_extraction_or_index_definition or [])
        chunks.extend(pp.aggregation_methods or [])
        chunks.extend(pp.model_fitting or [])
        chunks.extend(pp.postprocessing or [])
        chunks.extend(pp.selection_or_tuning_steps or [])
    pwi = record.producer_workflow_inputs
    if pwi is not None:
        chunks.extend(pwi.data_types or [])
        chunks.extend(pwi.sources or [])
        chunks.extend(pwi.variables_or_covariates or [])
        chunks.extend(pwi.method_specific_requirements or [])
    return " ".join(str(x) for x in chunks if x)


def remove_unsupported_application_only_analysis_types(record: FAIRagroApplicationDataFitnessModel) -> None:
    """Remove guarded analysis labels that are unsupported by the producer workflow.

    Application areas and potential uses are intentionally excluded from the evidence text.
    This prevents a paper saying that its data *could be used for* remote sensing, calibration,
    or another guarded activity from being mislabeled as performing that analysis itself.
    """
    ac = record.analysis_characteristics
    if ac is None or not ac.analysis_type:
        return
    producer = _producer_workflow_text(record)
    kept = []
    for item in ac.analysis_type:
        value = getattr(item, "value", str(item))
        guard = _ANALYSIS_WORKFLOW_GUARDS.get(value)
        if guard is not None and not guard.search(producer):
            continue
        kept.append(item)
    ac.analysis_type = kept or None


def postprocess_semantics(record: FAIRagroApplicationDataFitnessModel) -> FAIRagroApplicationDataFitnessModel:
    record = normalize_interval_terminology(record)
    ensure_interval_support_limitation(record)
    ensure_supported_spatial_applicability_limitation(record)
    normalize_dataset_family_scope_labels(record)
    remove_unsupported_application_only_analysis_types(record)
    enrich_quantitative_values(record)
    deduplicate_canonical_metrics(record)
    return record
