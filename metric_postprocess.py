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
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from models import (
    AggregationStatistic,
    EvidenceRecord,
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



def _metric_name_identity(name: str) -> str:
    """Conservative semantic identity for canonical validation metric names.

    Common aliases collapse to their base family.  Only qualifiers that clearly
    change the statistic itself are retained.  Display wording such as
    ``median MAE`` is handled by structured aggregation metadata instead.
    """

    text = re.sub(r"\s+", " ", (name or "").strip().lower())
    family = _metric_family(name or "")
    qualifiers: list[str] = []
    for qualifier, rx in (
        ("range", r"\brange\b"),
        ("maximum", r"\bmax(?:imum)?\b"),
        ("minimum", r"\bmin(?:imum)?\b"),
    ):
        if re.search(rx, text):
            qualifiers.append(qualifier)
    return family + ("_" + "_".join(qualifiers) if qualifiers else "")


def _target_token_key(label: str | None) -> tuple[str, ...]:
    tokens = re.findall(r"[a-z0-9]+", (label or "").lower())
    stop = {"the", "a", "an", "of", "for", "in", "on", "at", "to"}
    return tuple(sorted(x for x in tokens if x not in stop))


def _number_key(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value or ""))


def _quantitative_value_key(metric: ValidationMetricRecord) -> tuple[Any, ...] | None:
    """Return a display-independent numeric identity for a metric.

    Aggregation, nominal level and range-basis wording are intentionally not in
    this key.  They are complementary semantic metadata and are checked for
    compatibility separately so that a richer record can absorb a weaker one.
    """

    q = metric.quantitative_value
    if q is None:
        return None

    points = tuple(
        (
            re.sub(r"\s+", " ", point.label.strip().lower()),
            _number_key(point.value),
            _enum_value(point.role),
        )
        for point in (q.points or [])
    )
    uncertainty = tuple(
        (
            re.sub(r"\s+", " ", component.name.strip().lower()),
            _number_key(component.value),
            (component.unit_code or "").strip().lower(),
            _enum_value(component.scale),
        )
        for component in (q.uncertainty_components or [])
    )
    return (
        _enum_value(q.kind),
        _number_key(q.numeric_value),
        _number_key(q.lower_bound),
        _number_key(q.upper_bound),
        (q.unit_code or "").strip().lower(),
        points,
        uncertainty,
    )


def canonical_metric_identity(metric: ValidationMetricRecord) -> tuple[Any, ...]:
    """Identity of one canonical metric independent of display formatting."""

    q_key = _quantitative_value_key(metric)
    # Text-only summaries cannot safely be collapsed merely because their target
    # and name match.  Keep the normalized display text in the fallback key.
    value_key: Any = q_key if q_key is not None else re.sub(
        r"\s+", " ", (metric.value_or_summary or "").strip().lower()
    )
    return (
        _metric_name_identity(metric.name),
        metric.context.value,
        metric.scope.value,
        _target_token_key(metric.scope_label),
        metric.evaluation_support.value,
        value_key,
    )


def _generic_range_basis(value: str | None) -> bool:
    if not value:
        return True
    text = re.sub(r"\s+", " ", value.strip().lower())
    return text in {
        "reported range (basis not stated)",
        "reported interval or range (type not stated)",
        "reported min-max or across-group range",
    }


def _metric_modifiers_compatible(a: ValidationMetricRecord, b: ValidationMetricRecord) -> bool:
    qa = a.quantitative_value
    qb = b.quantitative_value
    if qa is None or qb is None:
        return True

    agg_a = qa.aggregation
    agg_b = qb.aggregation
    if (
        agg_a != AggregationStatistic.unspecified
        and agg_b != AggregationStatistic.unspecified
        and agg_a != agg_b
    ):
        return False

    if qa.nominal_level is not None and qb.nominal_level is not None and qa.nominal_level != qb.nominal_level:
        return False

    if (
        not _generic_range_basis(qa.range_basis)
        and not _generic_range_basis(qb.range_basis)
        and re.sub(r"\s+", " ", qa.range_basis.strip().lower())
        != re.sub(r"\s+", " ", qb.range_basis.strip().lower())
    ):
        return False
    return True



def canonical_metrics_equivalent(a: ValidationMetricRecord, b: ValidationMetricRecord) -> bool:
    """Return True when two records represent the same canonical statistic."""

    return canonical_metric_identity(a) == canonical_metric_identity(b) and _metric_modifiers_compatible(a, b)

def _norm_evidence_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _evidence_source_anchor(ev: EvidenceRecord) -> tuple[str, ...]:
    """Return the stable authored-source anchor for one evidence record."""
    return (
        _norm_evidence_text(ev.source_item_id or ev.source_location),
        _norm_evidence_text(ev.source_section),
        str(ev.source_page or ""),
        _enum_value(ev.source_modality).lower(),
        _enum_value(ev.representation_method).lower(),
    )


def _evidence_identity(ev: EvidenceRecord) -> tuple[str, ...]:
    """Conservative identity for evidence inside one canonical metric.

    When a concrete source fragment is available, that fragment plus its authored-source
    anchor identifies the support instance.  This deliberately ignores superficial claim
    paraphrases such as ``Target median MAE is 5.7 days`` versus ``Target: MAE 5.7``.
    If source_text is unavailable, fall back to the normalized claim so distinct claims
    are never collapsed merely because they cite the same table/figure/section.
    """
    support_text = _norm_evidence_text(ev.source_text)
    if not support_text:
        support_text = "claim:" + _norm_evidence_text(ev.claim)
    return _evidence_source_anchor(ev) + (support_text,)


def _review_rank(ev: EvidenceRecord) -> int:
    value = _enum_value(ev.review_status).lower()
    return {
        "machine_extracted": 0,
        "human_verified": 1,
        "human_corrected": 2,
    }.get(value, 0)


def _evidence_richness(ev: EvidenceRecord) -> int:
    score = 0
    score += min(len((ev.claim or "").strip()), 120)
    if ev.source_text:
        score += 25 + min(len(ev.source_text.strip()), 120)
    if ev.source_item_id:
        score += 8
    if ev.source_page is not None:
        score += 5
    if ev.source_location:
        score += 4
    if ev.source_section:
        score += 4
    if _enum_value(ev.source_modality).lower() != "unknown":
        score += 3
    if _enum_value(ev.representation_method).lower() != "unknown":
        score += 3
    if ev.source_asset_sha256:
        score += 2
    if ev.representation_confidence is not None:
        score += 1
    if ev.confidence_note:
        score += 1
    score += 10 * _review_rank(ev)
    return score


def _merge_complementary_evidence(primary: EvidenceRecord, secondary: EvidenceRecord) -> EvidenceRecord:
    """Fill missing provenance fields without rewriting an existing supported claim."""
    for field in (
        "source_section",
        "source_location",
        "source_text",
        "source_page",
        "source_item_id",
        "source_asset_sha256",
        "representation_confidence",
        "confidence_note",
    ):
        if getattr(primary, field, None) in (None, "") and getattr(secondary, field, None) not in (None, ""):
            setattr(primary, field, deepcopy(getattr(secondary, field)))

    if _enum_value(primary.source_modality).lower() == "unknown" and _enum_value(secondary.source_modality).lower() != "unknown":
        primary.source_modality = secondary.source_modality
    if _enum_value(primary.representation_method).lower() == "unknown" and _enum_value(secondary.representation_method).lower() != "unknown":
        primary.representation_method = secondary.representation_method
    if _review_rank(secondary) > _review_rank(primary):
        primary.review_status = secondary.review_status
    return primary


def _merge_metric_evidence(primary: ValidationMetricRecord, secondary: ValidationMetricRecord) -> None:
    merged: list[EvidenceRecord] = []
    positions: dict[tuple[str, ...], int] = {}
    for ev in list(primary.evidence or []) + list(secondary.evidence or []):
        key = _evidence_identity(ev)
        idx = positions.get(key)
        if idx is None:
            positions[key] = len(merged)
            merged.append(deepcopy(ev))
            continue

        existing = merged[idx]
        if _evidence_richness(ev) > _evidence_richness(existing):
            winner = deepcopy(ev)
            merged[idx] = _merge_complementary_evidence(winner, existing)
        else:
            _merge_complementary_evidence(existing, ev)
    primary.evidence = merged


def _metric_richness(metric: ValidationMetricRecord) -> int:
    score = 0
    if metric.unit:
        score += 1
    if metric.evaluation_population:
        score += 2
    if metric.interpretation:
        score += 2
    if metric.value_or_summary:
        score += min(len(metric.value_or_summary.strip()), 40)

    q = metric.quantitative_value
    if q is not None:
        score += 10
        if q.unit_code:
            score += 2
        if q.aggregation != AggregationStatistic.unspecified:
            score += 5
        if q.nominal_level is not None:
            score += 4
        if q.range_basis and not _generic_range_basis(q.range_basis):
            score += 4
        score += 2 * len(q.points or [])
        score += 2 * len(q.uncertainty_components or [])
    return score


def _merge_complementary_metric_metadata(
    primary: ValidationMetricRecord, secondary: ValidationMetricRecord
) -> ValidationMetricRecord:
    """Merge a weaker equivalent record into the richer canonical record."""

    if not primary.unit and secondary.unit:
        primary.unit = secondary.unit
    if not primary.evaluation_population and secondary.evaluation_population:
        primary.evaluation_population = secondary.evaluation_population
    if not primary.interpretation and secondary.interpretation:
        primary.interpretation = secondary.interpretation

    qp = primary.quantitative_value
    qs = secondary.quantitative_value
    if qp is None and qs is not None:
        primary.quantitative_value = deepcopy(qs)
    elif qp is not None and qs is not None:
        if not qp.unit_code and qs.unit_code:
            qp.unit_code = qs.unit_code
        if qp.aggregation == AggregationStatistic.unspecified and qs.aggregation != AggregationStatistic.unspecified:
            qp.aggregation = qs.aggregation
        if qp.nominal_level is None and qs.nominal_level is not None:
            qp.nominal_level = qs.nominal_level
        if _generic_range_basis(qp.range_basis) and not _generic_range_basis(qs.range_basis):
            qp.range_basis = qs.range_basis
        if not qp.points and qs.points:
            qp.points = deepcopy(qs.points)
        if not qp.uncertainty_components and qs.uncertainty_components:
            qp.uncertainty_components = deepcopy(qs.uncertainty_components)

    _merge_metric_evidence(primary, secondary)
    return primary


def deduplicate_canonical_metrics(record: FAIRagroApplicationDataFitnessModel) -> int:
    """Collapse semantically equivalent canonical validation metrics.

    The function is deliberately conservative: target, context, scope, evaluation
    support, metric identity, structured numeric value and canonical unit must all
    agree.  Conflicting non-default aggregation, nominal-level or range-basis
    metadata prevents a merge.  Equivalent records keep the richer metadata and
    union their source evidence.
    """

    vad = record.validation_and_diagnostics
    if vad is None or not vad.validation_metrics:
        return 0

    kept: list[ValidationMetricRecord] = []
    positions: dict[tuple[Any, ...], list[int]] = {}
    removed = 0

    for metric in vad.validation_metrics:
        key = canonical_metric_identity(metric)
        candidate_positions = positions.get(key, [])
        match_index = next(
            (idx for idx in candidate_positions if _metric_modifiers_compatible(kept[idx], metric)),
            None,
        )
        if match_index is None:
            kept.append(deepcopy(metric))
            positions.setdefault(key, []).append(len(kept) - 1)
            continue

        existing = kept[match_index]
        if _metric_richness(metric) > _metric_richness(existing):
            winner = deepcopy(metric)
            _merge_complementary_metric_metadata(winner, existing)
            kept[match_index] = winner
        else:
            _merge_complementary_metric_metadata(existing, metric)
        removed += 1

    vad.validation_metrics = kept or None
    return removed

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
