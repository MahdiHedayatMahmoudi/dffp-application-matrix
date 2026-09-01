"""Deterministic consolidation of an initial extraction and a semantic-repair result.

The repair model is allowed to correct semantic fields, but a repair must not silently
throw away source-supported canonical metrics or claim-level evidence already present in
the initial extraction.  This module therefore treats repair as an additive/corrective
patch rather than a wholesale replacement.

All rules are document-agnostic.  They operate on schema semantics, metric identities,
source evidence, target labels, and explicit parameter-language cues.  No paper names,
places, crop names, values, or figure/table numbers are encoded here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable

from models import (
    EvidenceRecord,
    EvidenceType,
    FAIRagroApplicationDataFitnessModel,
    MetricContext,
    MetricScope,
    TuningParameterRecord,
    SourceModality,
    RepresentationMethod,
    UncertaintyEvidence,
    UncertaintyScope,
    ValidationAndDiagnostics,
    ValidationMetricRecord,
)
from metric_postprocess import derive_fitness_metrics_from_canonical_ledger


@dataclass
class ConsolidationReport:
    initial_metrics: int = 0
    repair_metrics: int = 0
    merged_metrics: int = 0
    preserved_initial_metrics: int = 0
    deduplicated_metrics: int = 0
    composite_metrics_demoted: int = 0
    tuning_parameters_moved: int = 0
    evidence_records_preserved: int = 0
    related_resources_preserved: int = 0
    scopes_harmonized: int = 0
    uncertainty_views_added: int = 0
    structured_candidates_backfilled: int = 0
    evidence_records_deduplicated: int = 0
    cross_target_evidence_removed: int = 0
    non_outcome_metrics_demoted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _target_token_key(label: str | None) -> tuple[str, ...]:
    """Order-insensitive target key used only for conservative scope harmonization.

    This makes formatting variants such as ``Stage B (phase 18)`` and
    ``Stage B phase (18)`` comparable without any domain-specific vocabulary.
    """
    tokens = re.findall(r"[a-z0-9]+", _norm_text(label))
    stop = {"the", "a", "an", "of", "for", "in", "on", "at", "to"}
    kept = [x for x in tokens if x not in stop]
    return tuple(sorted(kept))


def _metric_family(name: str | None) -> str:
    text = _norm_text(name)
    patterns = (
        ("mae", r"\bmae\b|mean absolute error"),
        ("rmse", r"\brmse\b|root mean squared error"),
        ("mse", r"\bmse\b|mean squared error"),
        ("picp", r"\bpicp\b|prediction[- ]interval coverage"),
        ("mpiw", r"\bmpiw\b|mean prediction[- ]interval width|mean interval width"),
        ("bse", r"\bbse\b|bayesian posterior standard error"),
        ("r2", r"\br2\b|r\^2|r²|coefficient of determination"),
        ("correlation", r"\bcorrelation\b|pearson\s+r\b|spearman"),
        ("auc", r"\bauc\b|\bauroc\b|area under .* curve"),
        ("f1", r"\bf1\b|f1[- ]score"),
    )
    for family, rx in patterns:
        if re.search(rx, text, re.I):
            return family
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:100] or "metric"


def _metric_identity(name: str | None) -> str:
    family = _metric_family(name)
    text = _norm_text(name)
    qualifiers: list[str] = []
    for qualifier, rx in (
        ("range", r"\brange\b"),
        ("median", r"\bmedian\b"),
        ("mean", r"\bmean\b"),
        ("maximum", r"\bmax(?:imum)?\b"),
        ("minimum", r"\bmin(?:imum)?\b"),
    ):
        if re.search(rx, text):
            qualifiers.append(qualifier)
    return family + ("_" + "_".join(qualifiers) if qualifiers else "")


def _metric_value_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _metric_key(metric: ValidationMetricRecord) -> tuple[str, str, tuple[str, ...], str, str]:
    return (
        _metric_identity(metric.name),
        metric.context.value,
        _target_token_key(metric.scope_label),
        metric.evaluation_support.value,
        _metric_value_key(metric.value_or_summary),
    )


def _section_anchor(text: str | None) -> str:
    raw = _norm_text(text)
    m = re.match(r"^(?:section|sect\.?\s*)?(\d+(?:\.\d+)*)", raw)
    return m.group(1) if m else raw


def _evidence_key(ev: EvidenceRecord) -> tuple[str, str, str, str]:
    # ``source_text`` is intentionally excluded: repair and initial extraction often carry
    # slightly different truncations of the same authored evidence.  Claim + source anchor
    # + modality is the safer deterministic identity.
    anchor = _norm_text(ev.source_item_id) or _norm_text(ev.source_location)
    return (
        _norm_text(ev.claim),
        anchor,
        _section_anchor(ev.source_section),
        _norm_text(getattr(ev.source_modality, "value", str(ev.source_modality or ""))),
    )


def _merge_evidence(primary: Iterable[EvidenceRecord] | None, secondary: Iterable[EvidenceRecord] | None) -> tuple[list[EvidenceRecord], int]:
    out: list[EvidenceRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    added_secondary = 0
    for source, is_secondary in ((primary or [], False), (secondary or [], True)):
        for ev in source:
            key = _evidence_key(ev)
            if key in seen:
                continue
            seen.add(key)
            out.append(deepcopy(ev))
            if is_secondary:
                added_secondary += 1
    return out, added_secondary


def _merge_metric_ledgers(
    initial: list[ValidationMetricRecord], repair: list[ValidationMetricRecord], report: ConsolidationReport,
) -> list[ValidationMetricRecord]:
    # Repair records win on an exact duplicate, but initial evidence is retained.
    by_key: dict[tuple[str, str, tuple[str, ...], str, str], ValidationMetricRecord] = {}
    initial_by_key = {_metric_key(m): m for m in initial}
    for metric in repair:
        key = _metric_key(metric)
        m = deepcopy(metric)
        if key in initial_by_key:
            m.evidence, added = _merge_evidence(m.evidence, initial_by_key[key].evidence)
            report.evidence_records_preserved += added
        if key in by_key:
            existing = by_key[key]
            existing.evidence, added = _merge_evidence(existing.evidence, m.evidence)
            report.evidence_records_preserved += added
            report.deduplicated_metrics += 1
        else:
            by_key[key] = m

    for metric in initial:
        key = _metric_key(metric)
        if key in by_key:
            continue
        by_key[key] = deepcopy(metric)
        report.preserved_initial_metrics += 1

    merged = list(by_key.values())
    report.deduplicated_metrics += max(0, len(initial) + len(repair) - len(merged) - report.preserved_initial_metrics)
    return merged


def _recognized_metric_families(text: str) -> set[str]:
    families: set[str] = set()
    for family, rx in (
        ("mae", r"\bmae\b|mean absolute error"),
        ("rmse", r"\brmse\b|root mean squared error"),
        ("mse", r"\bmse\b|mean squared error"),
        ("picp", r"\bpicp\b|prediction[- ]interval coverage"),
        ("mpiw", r"\bmpiw\b|mean prediction[- ]interval width|mean interval width"),
        ("bse", r"\bbse\b|bayesian posterior standard error"),
        ("r2", r"\br2\b|r\^2|r²|coefficient of determination"),
        ("auc", r"\bauc\b|\bauroc\b|area under .* curve"),
        ("f1", r"\bf1\b|f1[- ]score"),
    ):
        if re.search(rx, text, re.I):
            families.add(family)
    return families


def _is_composite_summary(metric: ValidationMetricRecord) -> bool:
    name_family = _metric_family(metric.name)
    # A recognized atomic metric remains atomic even if its evidence mentions a sibling.
    if name_family in {"mae", "rmse", "mse", "picp", "mpiw", "bse", "r2", "auc", "f1", "correlation"}:
        return False
    text = " ".join(
        [metric.name, metric.value_or_summary or "", metric.interpretation or ""]
        + [ev.source_text or "" for ev in metric.evidence]
    )
    return len(_recognized_metric_families(text)) >= 2


def _section_numbers(metric: ValidationMetricRecord) -> set[str]:
    out: set[str] = set()
    for ev in metric.evidence:
        m = re.match(r"^\s*(\d+(?:\.\d+)*)", ev.source_section or "")
        if m:
            out.add(m.group(1))
    return out


def _section_related(a: ValidationMetricRecord, b: ValidationMetricRecord) -> bool:
    aa, bb = _section_numbers(a), _section_numbers(b)
    if not aa or not bb:
        return True
    for x in aa:
        for y in bb:
            if x == y or x.startswith(y + ".") or y.startswith(x + "."):
                return True
    return False



def _backfill_selected_structured_siblings(record: FAIRagroApplicationDataFitnessModel, bundle: Any, report: ConsolidationReport) -> None:
    """Backfill an omitted structured-table sibling only from explicit source candidates.

    A candidate is materialized only when (a) the same source row/target is already selected
    in the canonical ledger and (b) another target from the same authored source already
    establishes the metric's context/support semantics.  This avoids guessing metric meaning
    from a column name while making structured sibling completeness deterministic.
    """
    if bundle is None:
        return
    hints = [h for h in (getattr(bundle, "metric_candidate_hints", []) or []) if h.get("kind") == "structured_table_metric"]
    vad = record.validation_and_diagnostics
    if vad is None or not hints:
        return
    metrics = list(vad.validation_metrics or [])
    if not metrics:
        return

    def evidence_items(metric: ValidationMetricRecord) -> set[str]:
        return {str(ev.source_item_id or "") for ev in metric.evidence if ev.source_item_id}

    # Existing target/source selections and metric-family prototypes.
    selected: dict[tuple[str, tuple[str, ...]], list[ValidationMetricRecord]] = {}
    identity_proto: dict[tuple[str, str], ValidationMetricRecord] = {}
    existing_candidate_keys: set[tuple[str, tuple[str, ...], str, str]] = set()
    for metric in metrics:
        target = _target_token_key(metric.scope_label)
        identity = _metric_identity(metric.name)
        for iid in evidence_items(metric):
            selected.setdefault((iid, target), []).append(metric)
            identity_proto.setdefault((iid, identity), metric)
            existing_candidate_keys.add((iid, target, identity, _metric_value_key(metric.value_or_summary)))

    for hint in hints:
        iid = str(hint.get("source_item_id") or "")
        target_label = str(hint.get("target") or "").strip()
        target = _target_token_key(target_label)
        metric_name = str(hint.get("metric_name") or hint.get("column") or "").strip()
        identity = _metric_identity(metric_name)
        value = str(hint.get("value") or "").strip()
        if not iid or not target or not metric_name or not value:
            continue
        target_protos = selected.get((iid, target)) or []
        metric_proto = identity_proto.get((iid, identity))
        # When this exact statistic has no prototype, a sibling with the same base family
        # can still establish context/support semantics (e.g. MAE -> MAE range).
        if metric_proto is None:
            base_family = _metric_family(metric_name)
            metric_proto = next((m for m in metrics if iid in evidence_items(m) and _metric_family(m.name) == base_family), None)
        if not target_protos or metric_proto is None:
            continue
        if (iid, target, identity, _metric_value_key(value)) in existing_candidate_keys:
            continue
        if any(_metric_identity(m.name) == identity and _metric_value_key(m.value_or_summary) == _metric_value_key(value) for m in target_protos):
            continue
        target_proto = target_protos[0]
        section = hint.get("section_hint")
        location = hint.get("source_location")
        page = hint.get("source_page")
        claim = f"{target_label}: {metric_name} {value}." if target_label else f"{metric_name} {value}."
        ev = EvidenceRecord(
            claim=claim,
            evidence_type=EvidenceType.explicit,
            source_section=str(section) if section else None,
            source_location=str(location) if location else None,
            source_text=claim,
            source_page=int(page) if isinstance(page, int) or (isinstance(page, str) and page.isdigit()) else None,
            source_item_id=iid,
            source_modality=SourceModality.author_table,
            representation_method=RepresentationMethod.docling_structured,
        )
        new_metric = ValidationMetricRecord(
            name=metric_name,
            value_or_summary=value,
            unit=metric_proto.unit,
            context=metric_proto.context,
            scope=target_proto.scope,
            scope_label=target_proto.scope_label or target_label,
            evaluation_support=metric_proto.evaluation_support,
            evaluation_population=target_proto.evaluation_population or metric_proto.evaluation_population,
            interpretation=None,
            evidence=[ev],
        )
        metrics.append(new_metric)
        selected.setdefault((iid, target), []).append(new_metric)
        existing_candidate_keys.add((iid, target, identity, _metric_value_key(value)))
        report.structured_candidates_backfilled += 1

    vad.validation_metrics = metrics or None


def _demote_redundant_composites(record: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> None:
    vad = record.validation_and_diagnostics
    if vad is None or not vad.validation_metrics:
        return
    metrics = list(vad.validation_metrics)
    kept: list[ValidationMetricRecord] = []
    demoted_evidence: list[EvidenceRecord] = list(vad.evidence or [])
    for metric in metrics:
        if not _is_composite_summary(metric):
            kept.append(metric)
            continue
        text = " ".join([metric.name, metric.value_or_summary or ""] + [ev.source_text or "" for ev in metric.evidence])
        families = _recognized_metric_families(text)
        represented = set()
        for other in metrics:
            if other is metric or _is_composite_summary(other):
                continue
            if other.context != metric.context or other.scope != metric.scope:
                continue
            if not _section_related(metric, other):
                continue
            represented.add(_metric_family(other.name))
        if families and families.issubset(represented):
            demoted_evidence, added = _merge_evidence(demoted_evidence, metric.evidence)
            report.evidence_records_preserved += added
            report.composite_metrics_demoted += 1
            continue
        kept.append(metric)
    vad.validation_metrics = kept or None
    vad.evidence = demoted_evidence or None


def _domain_scope_rank(scope: MetricScope) -> int:
    return {
        MetricScope.crop: 1,
        MetricScope.crop_phase: 2,
        MetricScope.phase_year: 3,
    }.get(scope, 0)


def _harmonize_target_scopes(record: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> None:
    vad = record.validation_and_diagnostics
    if vad is None or not vad.validation_metrics:
        return
    groups: dict[tuple[str, ...], list[ValidationMetricRecord]] = {}
    for metric in vad.validation_metrics:
        key = _target_token_key(metric.scope_label)
        if len(key) >= 2:
            groups.setdefault(key, []).append(metric)
    generic_fallback = {MetricScope.unknown, MetricScope.subgroup, MetricScope.variable_or_layer}
    for group in groups.values():
        domain_scopes = [m.scope for m in group if _domain_scope_rank(m.scope)]
        if not domain_scopes:
            continue
        preferred = max(domain_scopes, key=_domain_scope_rank)
        for metric in group:
            if metric.scope in generic_fallback and metric.scope != preferred:
                metric.scope = preferred
                report.scopes_harmonized += 1


def _demote_non_outcome_metric_definitions(record: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> None:
    """Remove method definitions that do not report an outcome from the canonical ledger."""
    vad = record.validation_and_diagnostics
    if vad is None or not vad.validation_metrics:
        return
    kept: list[ValidationMetricRecord] = []
    section_evidence = list(vad.evidence or [])
    for metric in vad.validation_metrics:
        text = _norm_text(" ".join([metric.value_or_summary or "", metric.interpretation or ""]))
        no_outcome = bool(re.search(r"\bno standalone numerical (?:summary|value|result)\b|\bnot an independently reported validation result\b", text))
        if not no_outcome:
            kept.append(metric)
            continue
        section_evidence, added = _merge_evidence(section_evidence, metric.evidence)
        report.evidence_records_preserved += added
        report.non_outcome_metrics_demoted += 1
    vad.validation_metrics = kept or None
    vad.evidence = section_evidence or None


def _numeric_tokens(value: str | None) -> set[str]:
    return {m.group(0).replace(",", ".") for m in re.finditer(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?", value or "")}


def _target_word_set(label: str | None) -> set[str]:
    stop = {"the", "a", "an", "of", "for", "in", "on", "at", "to"}
    return {x for x in re.findall(r"[a-z]+", _norm_text(label)) if len(x) >= 3 and x not in stop}


def _remove_cross_target_metric_evidence(record: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> None:
    """Keep child-target numerical evidence out of a broader parent metric record."""
    vad = record.validation_and_diagnostics
    if vad is None or not vad.validation_metrics:
        return
    metrics = list(vad.validation_metrics)
    for metric in metrics:
        family = _metric_family(metric.name)
        parent_words = _target_word_set(metric.scope_label)
        if not parent_words:
            continue
        current_nums = _numeric_tokens(metric.value_or_summary)
        children = []
        for other in metrics:
            if other is metric or _metric_family(other.name) != family or other.context != metric.context:
                continue
            child_words = _target_word_set(other.scope_label)
            if parent_words < child_words:
                children.append((other, _numeric_tokens(other.value_or_summary)))
        if not children:
            continue
        kept: list[EvidenceRecord] = []
        for ev in metric.evidence or []:
            text_nums = _numeric_tokens(ev.source_text) | _numeric_tokens(ev.claim)
            current_supported = bool(current_nums & text_nums)
            child_supported = any(nums & text_nums for _other, nums in children if nums)
            if child_supported and not current_supported:
                report.cross_target_evidence_removed += 1
                continue
            kept.append(ev)
        metric.evidence = kept


def _deduplicate_all_evidence(record: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> None:
    """Deduplicate evidence arrays recursively using claim + source anchor identity."""
    from pydantic import BaseModel

    def visit(obj: Any) -> None:
        if isinstance(obj, BaseModel):
            if hasattr(obj, "evidence"):
                evidence = getattr(obj, "evidence", None)
                if isinstance(evidence, list) and evidence and all(isinstance(x, EvidenceRecord) for x in evidence):
                    out: list[EvidenceRecord] = []
                    seen: set[tuple[str, str, str, str]] = set()
                    for ev in evidence:
                        key = _evidence_key(ev)
                        if key in seen:
                            report.evidence_records_deduplicated += 1
                            continue
                        seen.add(key)
                        out.append(ev)
                    setattr(obj, "evidence", out or None)
            for field_name in obj.__class__.model_fields:
                if field_name == "evidence":
                    continue
                visit(getattr(obj, field_name, None))
        elif isinstance(obj, list):
            for item in obj:
                visit(item)
        elif isinstance(obj, dict):
            for item in obj.values():
                visit(item)

    visit(record)


def _parameter_phrase(text: str) -> str | None:
    # Extract a short explicit parameter phrase, not a document-specific parameter name.
    patterns = (
        r"\b([A-Za-z0-9_.+-]+(?:[- ]+[A-Za-z0-9_.+-]+){0,3}\s+(?:multipliers?|thresholds?|hyperparameters?|parameters?|settings?))\b",
        r"\b((?:candidate\s+)?[A-Za-z0-9_.+-]+(?:[- ]+[A-Za-z0-9_.+-]+){0,2}\s+(?:values?|levels?))\b",
    )
    for rx in patterns:
        m = re.search(rx, text, re.I)
        if m:
            phrase = re.sub(r"\s+", " ", m.group(1)).strip(" .,:;")
            if 2 <= len(phrase.split()) <= 5:
                return phrase
    return None


def _is_parameter_like(metric: ValidationMetricRecord) -> bool:
    if metric.context != MetricContext.selection_or_tuning:
        return False
    name = _norm_text(metric.name)
    text = " ".join([metric.value_or_summary or "", metric.interpretation or ""] + [ev.source_text or "" for ev in metric.evidence])
    # Clear outcome/statistic names should remain quantitative diagnostics.
    if re.search(r"\b(correlation|score|loss|error|accuracy|auc|f1|r2|rmse|mae|mse|picp|mpiw|bias)\b", name, re.I):
        return False
    parameter_cue = re.search(
        r"\b(?:candidate|tested|evaluated|grid|range|set of|values?)\b[^.;\n]{0,80}\b(?:threshold|multiplier|hyperparameter|parameter|setting)\b"
        r"|\b(?:threshold|multiplier|hyperparameter|parameter|setting)s?\b[^.;\n]{0,100}\b(?:tested|evaluated|candidate|values?|levels?|=)",
        text,
        re.I,
    )
    return bool(parameter_cue)


def _move_tuning_parameters(record: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> None:
    vad = record.validation_and_diagnostics
    if vad is None or not vad.validation_metrics:
        return
    kept: list[ValidationMetricRecord] = []
    params = list(vad.tuning_parameters or [])
    existing = {(_norm_text(x.name), _metric_value_key(x.value_or_summary), _target_token_key(x.scope_label)) for x in params}
    for metric in vad.validation_metrics:
        if not _is_parameter_like(metric):
            kept.append(metric)
            continue
        source_text = " ".join([metric.value_or_summary or ""] + [ev.source_text or "" for ev in metric.evidence])
        name = _parameter_phrase(source_text) or metric.name
        param = TuningParameterRecord(
            name=name,
            value_or_summary=metric.value_or_summary,
            unit=metric.unit,
            scope=metric.scope,
            scope_label=metric.scope_label,
            interpretation=metric.interpretation,
            evidence=deepcopy(metric.evidence),
        )
        key = (_norm_text(param.name), _metric_value_key(param.value_or_summary), _target_token_key(param.scope_label))
        if key not in existing:
            params.append(param)
            existing.add(key)
        report.tuning_parameters_moved += 1
    vad.validation_metrics = kept or None
    vad.tuning_parameters = params or None


def _merge_repair_evidence(repaired: FAIRagroApplicationDataFitnessModel, initial: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> FAIRagroApplicationDataFitnessModel:
    """Preserve claim-level evidence arrays and related resources without restoring corrected semantic lists."""
    rp = repaired.model_dump(mode="json", by_alias=True, exclude_none=False)
    ip = initial.model_dump(mode="json", by_alias=True, exclude_none=False)

    def merge_node(r: Any, i: Any, path: tuple[str, ...]) -> Any:
        if isinstance(r, dict) and isinstance(i, dict):
            out = deepcopy(r)
            for key, iv in i.items():
                if key not in out:
                    continue
                rv = out[key]
                if key == "evidence" and isinstance(iv, list):
                    # Evidence list objects are handled as dictionaries here for schema-wide coverage.
                    def dict_ev_key(x: dict[str, Any]) -> tuple[str, str, str, str]:
                        anchor = _norm_text(x.get("source_item_id")) or _norm_text(x.get("source_location"))
                        return (_norm_text(x.get("claim")), anchor, _section_anchor(x.get("source_section")), _norm_text(x.get("source_modality")))
                    existing = {dict_ev_key(x) for x in (rv or []) if isinstance(x, dict)}
                    merged = list(rv or [])
                    for item in iv:
                        if not isinstance(item, dict):
                            continue
                        k = dict_ev_key(item)
                        if k not in existing:
                            merged.append(deepcopy(item))
                            existing.add(k)
                            report.evidence_records_preserved += 1
                    out[key] = merged or None
                else:
                    out[key] = merge_node(rv, iv, path + (key,))
            return out
        if isinstance(r, list) and isinstance(i, list):
            # Do not union ordinary semantic lists: repair may intentionally remove an invalid label.
            return deepcopy(r)
        return deepcopy(r)

    merged = merge_node(rp, ip, ())

    # Related resources are source entities rather than semantic classifications, so omission
    # during repair should not discard them.  Merge by persistent identifier, then name.
    rd = merged.get("document_metadata") or {}
    idoc = ip.get("document_metadata") or {}
    rr = list(rd.get("related_resources") or [])
    ir = list(idoc.get("related_resources") or [])
    seen: set[str] = set()
    for item in rr:
        key = _norm_text(item.get("identifier") or item.get("name")) if isinstance(item, dict) else ""
        if key:
            seen.add(key)
    for item in ir:
        if not isinstance(item, dict):
            continue
        key = _norm_text(item.get("identifier") or item.get("name"))
        if key and key not in seen:
            rr.append(deepcopy(item))
            seen.add(key)
            report.related_resources_preserved += 1
    if rd:
        rd["related_resources"] = rr or None
        merged["document_metadata"] = rd

    return FAIRagroApplicationDataFitnessModel.model_validate(merged)


def _ensure_interval_uncertainty_views(record: FAIRagroApplicationDataFitnessModel, report: ConsolidationReport) -> None:
    vad = record.validation_and_diagnostics
    dq = record.data_quality_dependencies
    if vad is None or not vad.validation_metrics or dq is None or dq.uncertainty_handling is None:
        return
    handling = dq.uncertainty_handling
    products = list(handling.products_or_measures or [])
    existing = {_metric_family(p.name) for p in products}
    for metric in vad.validation_metrics:
        if metric.context != MetricContext.cross_validated_interval_assessment:
            continue
        family = _metric_family(metric.name)
        if family in existing:
            # Add evidence to an existing conceptual view when it is missing.
            for p in products:
                if _metric_family(p.name) == family and not p.evidence:
                    p.evidence = deepcopy(metric.evidence)
            continue
        scope = (
            UncertaintyScope.station_or_observation
            if metric.evaluation_support.value in {"held_out_station", "observation_station"}
            else UncertaintyScope.unknown
        )
        products.append(UncertaintyEvidence(
            name=metric.name,
            scope=scope,
            assessment_context=metric.context,
            evaluation_support=metric.evaluation_support,
            evaluation_population=metric.evaluation_population,
            representation="Scalar/table assessment metric",
            interpretation=metric.interpretation,
            evidence=deepcopy(metric.evidence),
        ))
        existing.add(family)
        report.uncertainty_views_added += 1
    handling.products_or_measures = products or None


def consolidate_repair_record(
    initial: FAIRagroApplicationDataFitnessModel,
    repaired: FAIRagroApplicationDataFitnessModel,
    bundle: Any | None = None,
) -> tuple[FAIRagroApplicationDataFitnessModel, ConsolidationReport]:
    """Merge repair additions with the initial extraction while preserving corrections."""
    report = ConsolidationReport()
    record = _merge_repair_evidence(deepcopy(repaired), initial, report)

    if record.validation_and_diagnostics is None:
        record.validation_and_diagnostics = ValidationAndDiagnostics()
    initial_metrics = list(initial.validation_and_diagnostics.validation_metrics or []) if initial.validation_and_diagnostics else []
    repair_metrics = list(record.validation_and_diagnostics.validation_metrics or [])
    report.initial_metrics = len(initial_metrics)
    report.repair_metrics = len(repair_metrics)
    record.validation_and_diagnostics.validation_metrics = _merge_metric_ledgers(initial_metrics, repair_metrics, report) or None

    _backfill_selected_structured_siblings(record, bundle, report)
    _harmonize_target_scopes(record, report)
    _demote_redundant_composites(record, report)
    _demote_non_outcome_metric_definitions(record, report)
    _move_tuning_parameters(record, report)
    _remove_cross_target_metric_evidence(record, report)
    _ensure_interval_uncertainty_views(record, report)
    _deduplicate_all_evidence(record, report)

    # The canonical ledger is authoritative after consolidation.  Clear the old model-
    # generated/previously-derived views before rebuilding them so they cannot re-promote
    # stale duplicates with outdated scopes or composite summaries.  Formal thresholds are
    # intentionally preserved.
    if record.outputs_and_fitness_indicators and record.outputs_and_fitness_indicators.fitness_for_use_metrics:
        ff = record.outputs_and_fitness_indicators.fitness_for_use_metrics
        ff.producer_side_metrics = None
        ff.application_specific_metrics = None
    derive_fitness_metrics_from_canonical_ledger(record)

    report.merged_metrics = len(record.validation_and_diagnostics.validation_metrics or [])
    return record, report
