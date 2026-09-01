"""Deterministic normalization and reuse of quantitative metrics.

The preferred v3.2.2 contract is that the extraction model writes one canonical
quantitative metric ledger under ``validation_and_diagnostics.validation_metrics``.
Fitness summaries are then deterministic views of that ledger, not independent
copies.

For backward compatibility and robustness, any genuinely missing legacy metric
that appears only in a model-populated fitness list is first promoted into the
canonical ledger.  Equivalent metrics already present in the ledger are not
promoted again.
"""

from __future__ import annotations

from copy import deepcopy
import re

from models import (
    FAIRagroApplicationDataFitnessModel,
    FitnessForUseMetrics,
    MetricContext,
    OutputsAndFitnessIndicators,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)


_PRODUCER_FITNESS_CONTEXTS = {
    MetricContext.out_of_sample_validation,
    MetricContext.cross_validated_interval_assessment,
    MetricContext.spatial_uncertainty_summary,
}


def _metric_family(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if re.search(r"\bmae\b|mean absolute error", text):
        return "mae"
    if re.search(r"\brmse\b|root mean squared error", text):
        return "rmse"
    if re.search(r"\bpicp\b|prediction interval coverage", text):
        return "picp"
    if re.search(r"\bmpiw\b|mean prediction interval width", text):
        return "mpiw"
    if re.search(r"\bbse\b|bayesian posterior standard error", text):
        return "bse"
    return text


def _normalized_scope_label(label: str | None) -> str:
    text = (label or "").strip().lower()
    if ";" in text:
        parts = sorted(x.strip() for x in text.split(";") if x.strip())
        return "; ".join(parts)
    return text


def _metric_key(metric: ValidationMetricRecord) -> tuple[str, str, str, str]:
    return (
        _metric_family(metric.name),
        metric.context.value,
        metric.scope.value,
        _normalized_scope_label(metric.scope_label),
    )


def _promote_missing_legacy_fitness_metrics(record: FAIRagroApplicationDataFitnessModel) -> None:
    """Promote only fitness-only metrics that have no equivalent canonical record."""
    outputs = record.outputs_and_fitness_indicators
    fitness = outputs.fitness_for_use_metrics if outputs is not None else None
    if fitness is None:
        return

    if record.validation_and_diagnostics is None:
        record.validation_and_diagnostics = ValidationAndDiagnostics()
    vad = record.validation_and_diagnostics
    if vad.validation_metrics is None:
        vad.validation_metrics = []

    existing = {_metric_key(m) for m in vad.validation_metrics}
    legacy = list(fitness.producer_side_metrics or []) + list(fitness.application_specific_metrics or [])
    for metric in legacy:
        key = _metric_key(metric)
        if key not in existing:
            vad.validation_metrics.append(deepcopy(metric))
            existing.add(key)


def derive_fitness_metrics_from_canonical_ledger(
    record: FAIRagroApplicationDataFitnessModel,
    *,
    promote_legacy: bool = True,
) -> None:
    """Synchronize the canonical ledger, then derive the fitness metric views.

    Formal thresholds are never altered here.
    """

    if promote_legacy:
        _promote_missing_legacy_fitness_metrics(record)

    vad = record.validation_and_diagnostics
    metrics = list(vad.validation_metrics or []) if vad is not None else []

    # Fitness lists are views, not an independent source of truth.  Always clear
    # or rebuild them from the current canonical ledger.  Review derivation calls
    # this function with promote_legacy=False so a rejected canonical metric can
    # never be re-promoted from a stale machine-baseline mirror.
    outputs = record.outputs_and_fitness_indicators
    if outputs is None:
        if not metrics:
            return
        record.outputs_and_fitness_indicators = OutputsAndFitnessIndicators()
        outputs = record.outputs_and_fitness_indicators
    if outputs.fitness_for_use_metrics is None:
        if not metrics:
            return
        outputs.fitness_for_use_metrics = FitnessForUseMetrics()

    fitness = outputs.fitness_for_use_metrics
    if not metrics:
        fitness.producer_side_metrics = None
        fitness.application_specific_metrics = None
        return
    producer = [deepcopy(m) for m in metrics if m.context in _PRODUCER_FITNESS_CONTEXTS]
    downstream = [deepcopy(m) for m in metrics if m.context == MetricContext.downstream_use_case]

    fitness.producer_side_metrics = producer or None
    fitness.application_specific_metrics = downstream or None
