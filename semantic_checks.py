"""Deterministic post-extraction checks for high-risk semantic mistakes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import Counter
import re
from typing import Any

from models import FAIRagroApplicationDataFitnessModel
from quantitative_values import parse_quantitative_value
from source_bundle import SourceBundle
from semantic_postprocess import _ANALYSIS_WORKFLOW_GUARDS, _producer_workflow_text


@dataclass(frozen=True)
class CheckIssue:
    severity: str  # error | warning | info
    code: str
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))




_DEPRECATED_INTERVAL_PATTERNS = (
    re.compile(r"\bprediction[- ]interval calibration\b", re.IGNORECASE),
    re.compile(r"\bpredictive[- ]interval calibration\b", re.IGNORECASE),
    re.compile(r"\binterval-calibration\b", re.IGNORECASE),
    re.compile(r"\bcross_validated_interval_calibration\b", re.IGNORECASE),
    re.compile(r"\bprediction_interval_calibration\b", re.IGNORECASE),
)

# Source locators/quotes preserve authored wording and must not be normalized by
# deterministic terminology checks. Machine-generated semantic fields are checked.
_SOURCE_TEXT_KEYS = {
    "source_text",
    "source_section",
    "source_location",
}


def _iter_normalized_text(value: Any, path: str = "$"):
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            yield path, value.strip()
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            yield from _iter_normalized_text(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _SOURCE_TEXT_KEYS:
                continue
            yield from _iter_normalized_text(item, f"{path}.{key}")


def _deprecated_interval_terminology(payload: dict[str, Any]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path, text in _iter_normalized_text(payload):
        if any(p.search(text) for p in _DEPRECATED_INTERVAL_PATTERNS):
            hits.append((path, text))
    return hits


def _is_interval_metric_name(name: str | None) -> bool:
    if not name:
        return False
    upper = name.upper()
    return "PICP" in upper or "MPIW" in upper


def _interval_support_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    issues: list[CheckIssue] = []

    metric_groups: list[tuple[str, list[Any] | None]] = []
    vad = record.validation_and_diagnostics
    if vad is not None:
        metric_groups.append(("validation_and_diagnostics.validation_metrics", vad.validation_metrics))

    ofi = record.outputs_and_fitness_indicators
    if ofi is not None and ofi.fitness_for_use_metrics is not None:
        metric_groups.append((
            "outputs_and_fitness_indicators.fitness_for_use_metrics.producer_side_metrics",
            ofi.fitness_for_use_metrics.producer_side_metrics,
        ))
        metric_groups.append((
            "outputs_and_fitness_indicators.fitness_for_use_metrics.application_specific_metrics",
            ofi.fitness_for_use_metrics.application_specific_metrics,
        ))

    for base, metrics in metric_groups:
        for i, metric in enumerate(metrics or []):
            if _is_interval_metric_name(metric.name) and _enum_value(metric.evaluation_support) in {None, "unknown"}:
                issues.append(CheckIssue(
                    "warning",
                    "INTERVAL_METRIC_EVALUATION_SUPPORT_UNKNOWN",
                    f"{metric.name!r} does not state where the interval metric was evaluated. "
                    "Populate evaluation_support/evaluation_population when the source provides it; "
                    "do not imply station-based validation is pixel-wise raster validation.",
                    f"{base}[{i}].evaluation_support",
                ))

    dq = record.data_quality_dependencies
    products = None
    if dq is not None and dq.uncertainty_handling is not None:
        products = dq.uncertainty_handling.products_or_measures
    for i, product in enumerate(products or []):
        if _is_interval_metric_name(product.name) and _enum_value(product.evaluation_support) in {None, "unknown"}:
            issues.append(CheckIssue(
                "warning",
                "INTERVAL_PRODUCT_EVALUATION_SUPPORT_UNKNOWN",
                f"{product.name!r} does not state where the interval diagnostic was evaluated.",
                f"data_quality_dependencies.uncertainty_handling.products_or_measures[{i}].evaluation_support",
            ))

    return issues




def _metric_scope_consistency_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    """Check one-target/one-scope semantics without assuming a particular paper domain."""
    issues: list[CheckIssue] = []
    vad = record.validation_and_diagnostics
    metrics = list(vad.validation_metrics or []) if vad is not None else []
    dataset_name = ""
    if record.dataset_characteristics is not None:
        dataset_name = (record.dataset_characteristics.dataset_name or "").strip().lower()

    aggregate_terms = re.compile(r"\b(?:all|overall|entire|full|pooled|combined|aggregate|family|family[- ]wide|dataset family)\b", re.I)
    cardinal_aggregate = re.compile(
        r"^(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|multiple|many)|\d+)\s+"
        r"[A-Za-z][A-Za-z0-9_-]*s\b", re.I
    )
    target_scopes = {
        "subgroup", "variable_or_layer", "study_or_site", "experiment_or_treatment", "model_or_method",
        "spatial_subset", "temporal_subset", "spatiotemporal_subset", "crop", "crop_phase", "phase_year", "use_case",
    }
    support_like_scopes = {"local_or_pixel", "spatial_surface_summary", "station_or_observation"}

    def token_overlap(a: str, b: str) -> float:
        aa = {x for x in re.findall(r"[a-z0-9]+", a.lower()) if len(x) > 2}
        bb = {x for x in re.findall(r"[a-z0-9]+", b.lower()) if len(x) > 2}
        return len(aa & bb) / max(1, len(aa | bb))

    for i, metric in enumerate(metrics):
        base = f"validation_and_diagnostics.validation_metrics[{i}]"
        scope = _enum_value(metric.scope) or "unknown"
        label = (metric.scope_label or "").strip()
        label_lower = label.lower()

        if label and (";" in label or " | " in label):
            issues.append(CheckIssue(
                "error", "METRIC_SCOPE_LABEL_MULTIPLE_TARGETS",
                f"{metric.name!r} has a list-like scope_label {label!r}. One metric record must describe one target; split targets into atomic records.",
                f"{base}.scope_label",
            ))

        is_aggregate_label = bool(label and (aggregate_terms.search(label) or cardinal_aggregate.search(label)))
        if is_aggregate_label and scope in target_scopes - {"use_case"}:
            issues.append(CheckIssue(
                "error", "METRIC_SCOPE_LABEL_CONTRADICTS_SCOPE",
                f"{metric.name!r} uses scope={scope!r} but scope_label={label!r} reads as an aggregate/family-wide target.",
                f"{base}.scope",
            ))

        if scope == "dataset_family" and label:
            same_as_dataset = bool(dataset_name and token_overlap(label_lower, dataset_name) >= 0.50)
            if not is_aggregate_label and not same_as_dataset:
                issues.append(CheckIssue(
                    "error", "DATASET_FAMILY_SCOPE_WITH_SPECIFIC_TARGET",
                    f"{metric.name!r} uses scope='dataset_family' but scope_label={label!r} appears to name a specific target. "
                    "Use dataset/subgroup/variable_or_layer or another domain-appropriate specific scope instead.",
                    f"{base}.scope",
                ))

        if scope in target_scopes and not label:
            issues.append(CheckIssue(
                "warning", "METRIC_SCOPE_LABEL_MISSING",
                f"{metric.name!r} uses scope={scope!r} but has no scope_label identifying the target.",
                f"{base}.scope_label",
            ))

        if scope == "phase_year" and label and not re.search(r"\b(?:19|20)\d{2}\b", label):
            issues.append(CheckIssue(
                "warning", "PHASE_YEAR_SCOPE_WITHOUT_YEAR",
                f"{metric.name!r} is scoped as phase_year but scope_label={label!r} does not identify a specific year. Use crop_phase or a generic temporal/spatiotemporal scope for a multi-year summary.",
                f"{base}.scope",
            ))

        if scope in support_like_scopes and label and _enum_value(metric.evaluation_support) not in {None, "unknown"}:
            issues.append(CheckIssue(
                "error", "METRIC_SCOPE_MIXES_TARGET_AND_SUPPORT",
                f"{metric.name!r} uses support-like scope={scope!r} while also naming target {label!r} and setting evaluation_support. "
                "scope should describe the scientific target/aggregation; keep raster/station/spatial support in evaluation_support.",
                f"{base}.scope",
            ))

    return issues

def _quantitative_metric_evidence_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    """Require direct evidence and useful locators for each canonical quantitative metric."""
    issues: list[CheckIssue] = []
    vad = record.validation_and_diagnostics
    metrics = list(vad.validation_metrics or []) if vad is not None else []
    for i, metric in enumerate(metrics):
        base = f"validation_and_diagnostics.validation_metrics[{i}]"
        evidence = list(metric.evidence or [])
        if not evidence:
            issues.append(CheckIssue(
                "error",
                "QUANTITATIVE_METRIC_EVIDENCE_MISSING",
                f"Canonical quantitative metric {metric.name!r} has no evidence. Every metric must retain at least one source-grounded evidence record.",
                f"{base}.evidence",
            ))
            continue
        if not any(ev.source_item_id or ev.source_text or ev.source_section for ev in evidence):
            issues.append(CheckIssue(
                "warning",
                "QUANTITATIVE_METRIC_EVIDENCE_WEAK_LOCATOR",
                f"Canonical quantitative metric {metric.name!r} has evidence but no source text/item/section locator.",
                f"{base}.evidence",
            ))
    return issues


def _guarded_analysis_type_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    """Fallback validation for analysis labels prone to application-domain leakage."""
    issues: list[CheckIssue] = []
    ac = record.analysis_characteristics
    if ac is None:
        return issues
    analysis_values = {_enum_value(x) for x in (ac.analysis_type or [])}
    if not analysis_values:
        return issues
    producer = _producer_workflow_text(record)
    for label, guard in _ANALYSIS_WORKFLOW_GUARDS.items():
        if label not in analysis_values or guard.search(producer):
            continue
        code = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_") + "_ANALYSIS_WITHOUT_WORKFLOW_SUPPORT"
        issues.append(CheckIssue(
            "error", code,
            f"analysis_type includes {label!r} but the producer workflow/input/method description does not support that analysis. "
            "Do not promote a downstream application domain or potential use into the analysed workflow.",
            "analysis_characteristics.analysis_type",
        ))
    return issues

def _metric_sibling_split_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    """Detect obvious loss of a sibling metric when one evidence excerpt contains another numeric metric."""
    issues: list[CheckIssue] = []
    vad = record.validation_and_diagnostics
    metrics = list(vad.validation_metrics or []) if vad is not None else []
    if not metrics:
        return issues

    def family(name: str) -> str | None:
        text = name.lower()
        if re.search(r"\bmae\b|mean absolute error", text): return "MAE"
        if re.search(r"\brmse\b|root mean squared error", text): return "RMSE"
        if re.search(r"\bpicp\b|prediction[- ]interval coverage", text): return "PICP"
        if re.search(r"\bmpiw\b|mean prediction[- ]interval width", text): return "MPIW"
        if re.search(r"\bbse\b|bayesian posterior standard error", text): return "BSE"
        return None

    keys = {(family(m.name), _enum_value(m.scope), (m.scope_label or "").strip().lower()) for m in metrics}
    patterns = {
        "MAE": re.compile(r"(?:\bMAE\b|mean absolute error)[^.;\n]{0,80}?\d", re.I),
        "RMSE": re.compile(r"(?:\bRMSE\b|root mean squared error)[^.;\n]{0,80}?\d", re.I),
        "PICP": re.compile(r"(?:\bPICP\b|prediction[- ]interval coverage)[^.;\n]{0,80}?\d", re.I),
        "MPIW": re.compile(r"(?:\bMPIW\b|mean prediction[- ]interval width)[^.;\n]{0,80}?\d", re.I),
    }
    for i, metric in enumerate(metrics):
        current = family(metric.name)
        scope = _enum_value(metric.scope)
        label = (metric.scope_label or "").strip().lower()
        excerpts = " ".join(ev.source_text or "" for ev in (metric.evidence or []))
        if not excerpts:
            continue
        for sibling, pattern in patterns.items():
            if sibling == current or not pattern.search(excerpts):
                continue
            if (sibling, scope, label) not in keys:
                issues.append(CheckIssue(
                    "warning",
                    "METRIC_EVIDENCE_SUGGESTS_MISSING_SIBLING",
                    f"Evidence attached to {metric.name!r} appears to contain a numeric {sibling} for the same target/scope, but no atomic {sibling} record exists. "
                    "Check that splitting a multi-metric source statement did not discard a sibling statistic.",
                    f"validation_and_diagnostics.validation_metrics[{i}].evidence",
                ))
    return issues


def _normalize_candidate_target(text: str | None) -> str:
    value = (text or "").strip().lower()
    value = re.sub(r"\(\s*\d+\s*\)", "", value)  # common sample-count suffix
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _canonical_metric_key(name: str | None) -> str:
    text = (name or "").strip().lower()
    aliases = (
        ("mae", r"\bmae\b|mean absolute error"),
        ("rmse", r"\brmse\b|root mean squared error"),
        ("mse", r"\bmse\b|mean squared error"),
        ("picp", r"\bpicp\b|prediction[- ]interval coverage"),
        ("mpiw", r"\bmpiw\b|mean prediction[- ]interval width|mean interval width"),
        ("bse", r"\bbse\b|bayesian posterior standard error"),
        ("r2", r"\br2\b|r\^2|r²|coefficient of determination"),
        ("f1", r"\bf1\b|f1[- ]score"),
        ("auc", r"\bauc\b|\bauroc\b|area under .* curve"),
    )
    for key, rx in aliases:
        if re.search(rx, text, re.I):
            return key
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:80]


def _structured_metric_key(name: str | None) -> str:
    """Preserve statistic qualifiers for structured columns (e.g. MAE vs MAE range)."""
    text = (name or "").strip().lower()
    base = _canonical_metric_key(name)
    qualifiers: list[str] = []
    for q, rx in (("range", r"\brange\b"), ("median", r"\bmedian\b"), ("mean", r"\bmean\b"),
                  ("minimum", r"\bmin(?:imum)?\b"), ("maximum", r"\bmax(?:imum)?\b")):
        if re.search(rx, text):
            qualifiers.append(q)
    return base + ("_" + "_".join(qualifiers) if qualifiers else "")


def _target_tokens(text: str | None) -> set[str]:
    stop = {"the", "for", "from", "with", "phase", "group", "family", "data", "dataset", "metric", "mean", "median",
            "all", "overall", "entire", "full"}
    out: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(token) < 3 or token in stop:
            continue
        # Conservative English plural normalization improves compatibility between
        # labels such as ``crop family`` and ``eight crops`` without domain rules.
        if token.isalpha() and len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        out.add(token)
    return out


def _target_compatible(hint: str | None, label: str | None) -> bool:
    if not hint:
        return True
    a, b = _target_tokens(hint), _target_tokens(label)
    if not a or not b:
        return False
    return bool(a & b)


def _section_compatible(a: str | None, b: str | None) -> bool:
    aa = re.sub(r"\s+", " ", (a or "").strip().lower())
    bb = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not aa or not bb:
        return False
    if aa == bb or aa in bb or bb in aa:
        return True
    # Section-number hierarchy is useful across prose/caption representations: 3.3 and
    # 3.3.4 can be compatible without requiring identical generated section labels.
    ma = re.match(r"^(\d+(?:\.\d+)*)", aa)
    mb = re.match(r"^(\d+(?:\.\d+)*)", bb)
    if ma and mb:
        sa, sb = ma.group(1), mb.group(1)
        return sa == sb or sa.startswith(sb + ".") or sb.startswith(sa + ".")
    return False


def _prose_candidate_value_signature(hint: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Identify duplicate prose discoveries independently of noisy target hints."""

    pairs: list[tuple[str, str]] = []
    for pair in hint.get("quantitative_pairs") or []:
        key = str(pair.get("metric_key") or _canonical_metric_key(pair.get("metric_name")))
        value = re.sub(r"\s+", "", str(pair.get("value") or "").lower())
        if key and value:
            pairs.append((key, value))
    return tuple(sorted(pairs))

def _source_metric_completeness_issues(
    record: FAIRagroApplicationDataFitnessModel,
    bundle: SourceBundle,
) -> list[CheckIssue]:
    """Use generic source candidates to detect loss after an extraction target is selected.

    Candidate hints never create values. They only test whether an authored quantitative
    result that is DFFP-relevant was silently dropped from the canonical ledger.
    """
    issues: list[CheckIssue] = []
    hints = list(getattr(bundle, "metric_candidate_hints", []) or [])
    if not hints:
        return issues
    vad = record.validation_and_diagnostics
    metrics = list(vad.validation_metrics or []) if vad is not None else []

    # Structured tables: once a target row is selected, preserve every metric-like
    # sibling in that selected row. This works for unfamiliar paper-specific metrics.
    table_groups: dict[tuple[str, str], set[str]] = {}
    for hint in hints:
        if hint.get("kind") != "structured_table_metric":
            continue
        iid = str(hint.get("source_item_id") or "")
        target = _normalize_candidate_target(hint.get("target"))
        key = _structured_metric_key(hint.get("metric_name") or hint.get("metric_family") or hint.get("metric_key"))
        if iid and target and key:
            table_groups.setdefault((iid, target), set()).add(key)

    represented: dict[tuple[str, str], set[str]] = {}
    for metric in metrics:
        target = _normalize_candidate_target(metric.scope_label)
        if not target:
            continue
        key = _structured_metric_key(metric.name)
        for ev in metric.evidence or []:
            iid = str(ev.source_item_id or "")
            if iid:
                represented.setdefault((iid, target), set()).add(key)

    for group, found in represented.items():
        supported = table_groups.get(group)
        if not supported:
            continue
        missing = sorted(supported - found)
        if missing:
            iid, target = group
            issues.append(CheckIssue(
                "error", "STRUCTURED_TABLE_METRIC_SIBLINGS_MISSING",
                f"Structured source {iid} contains additional metric-like columns {', '.join(missing)} for already-selected target {target!r}. "
                "Atomic splitting must preserve supported sibling metrics rather than choosing only some columns.",
                "validation_and_diagnostics.validation_metrics",
            ))

    # Once a multi-target, multi-metric result table is selected, an aggregate row
    # and one example row are not a lossless representation of that table. Require
    # all candidate target rows when at least two recognized outcome metric columns
    # establish that the table is a quantitative evaluation table.
    represented_table_ids = {iid for iid, _target in represented}
    groups_by_table: dict[str, dict[str, set[str]]] = {}
    recognized_by_table: dict[str, set[str]] = {}
    for hint in hints:
        if hint.get("kind") != "structured_table_metric":
            continue
        iid = str(hint.get("source_item_id") or "")
        target = _normalize_candidate_target(hint.get("target"))
        key = _structured_metric_key(hint.get("metric_name") or hint.get("metric_key"))
        if not iid or not target or not key:
            continue
        groups_by_table.setdefault(iid, {}).setdefault(target, set()).add(key)
        if hint.get("metric_family"):
            recognized_by_table.setdefault(iid, set()).add(_canonical_metric_key(hint.get("metric_family")))

    for iid in sorted(represented_table_ids):
        target_rows = groups_by_table.get(iid, {})
        if len(target_rows) < 2 or len(recognized_by_table.get(iid, set())) < 2:
            continue
        missing_targets = sorted(target for target in target_rows if (iid, target) not in represented)
        if missing_targets:
            preview = ", ".join(repr(x) for x in missing_targets[:8])
            suffix = "" if len(missing_targets) <= 8 else f" and {len(missing_targets) - 8} more"
            issues.append(CheckIssue(
                "error", "STRUCTURED_TABLE_TARGET_ROWS_MISSING",
                f"Structured result table {iid} is already used as canonical metric evidence but omits supported target row(s): "
                f"{preview}{suffix}. Preserve all DFFP-relevant target/metric combinations or explicitly remove the table as evidence.",
                "validation_and_diagnostics.validation_metrics",
            ))

    # Prose/captions: summary-level quantitative claims in quality/evaluation sections
    # are high-value DFFP candidates. Match by section and, when available, target hint.
    emitted_prose_errors: set[tuple[str, str, str, tuple[str, ...]]] = set()
    # Sliding prose windows often discover the same numerical statement several
    # times. One window may mistake a procedural phrase (for example "for every
    # phase and year") for the scientific target, while a shorter window captures
    # the actual target. Group exact section + metric/value duplicates and allow a
    # represented peer target to resolve that discovery noise.
    prose_peer_targets: dict[tuple[str, tuple[tuple[str, str], ...]], set[str]] = {}
    for prose_hint in hints:
        if prose_hint.get("kind") != "prose_metric_candidate":
            continue
        signature = _prose_candidate_value_signature(prose_hint)
        section_key = re.sub(r"\s+", " ", str(prose_hint.get("source_section") or "").strip().lower())
        peer_target = str(prose_hint.get("target_hint") or "").strip()
        if signature and section_key and peer_target:
            prose_peer_targets.setdefault((section_key, signature), set()).add(peer_target)

    for hint in hints:
        if hint.get("kind") != "prose_metric_candidate":
            continue
        raw_keys = hint.get("result_metric_keys")
        if raw_keys is None:
            raw_keys = hint.get("metric_keys") or []
        keys = {str(x) for x in raw_keys if x}
        section = str(hint.get("source_section") or "")
        target_hint = str(hint.get("target_hint") or "") or None
        summary = bool(hint.get("summary_signal"))
        quality = bool(hint.get("quality_context"))
        downstream = bool(hint.get("downstream_use_case_result"))

        # A target-bearing prose candidate may be represented elsewhere in the same
        # subsection hierarchy (e.g. a figure caption under a parent example section).
        # Match the scientific target first; section equality is only a fallback anchor.
        target_aliases = {target_hint} if target_hint else set()
        signature = _prose_candidate_value_signature(hint)
        section_key = re.sub(r"\s+", " ", section.strip().lower())
        target_aliases.update(prose_peer_targets.get((section_key, signature), set()))
        target_metrics = [
            m for m in metrics
            if target_aliases and any(_target_compatible(alias, m.scope_label) for alias in target_aliases if alias)
        ]
        section_metrics = [
            m for m in metrics
            if any(_section_compatible(section, (ev.source_section or "")) for ev in (m.evidence or []))
        ]
        comparable = target_metrics if target_hint else section_metrics
        present = {_canonical_metric_key(m.name) for m in comparable}

        if quality and summary and keys:
            missing = sorted(keys - present)
            if missing:
                anchored = bool(target_hint or (present & keys))
                if anchored:
                    issue_key = ("summary", section, _normalize_candidate_target(target_hint), tuple(missing))
                    if issue_key not in emitted_prose_errors:
                        emitted_prose_errors.add(issue_key)
                        issues.append(CheckIssue(
                            "error", "SUMMARY_PROSE_METRIC_UNREPRESENTED",
                            f"A summary-level quantitative quality/evaluation statement in section {section!r} supports metric(s) {', '.join(missing)} "
                            f"for target hint {target_hint!r}, but no matching canonical record is present.",
                            "validation_and_diagnostics.validation_metrics",
                        ))

        if downstream:
            downstream_metrics = [m for m in metrics if _enum_value(m.context) == "downstream_use_case"]
            same_section = [
                m for m in downstream_metrics
                if any(_section_compatible(section, (ev.source_section or "")) for ev in (m.evidence or []))
            ]
            expected_keys = keys
            if target_hint:
                same_target = [m for m in downstream_metrics if _target_compatible(target_hint, m.scope_label)]
            else:
                same_target = []
            represented_keys = {_canonical_metric_key(m.name) for m in (same_section + same_target)}
            missing_keys = sorted(expected_keys - represented_keys) if expected_keys else []
            if not same_section and not same_target:
                issue_key = ("downstream-none", section, _normalize_candidate_target(target_hint), tuple(sorted(expected_keys)))
                if issue_key not in emitted_prose_errors:
                    emitted_prose_errors.add(issue_key)
                    issues.append(CheckIssue(
                        "error", "SOURCE_DOWNSTREAM_NUMERIC_RESULT_UNREPRESENTED",
                        f"A demonstrated/application section {section!r} contains an explicit numerical result, but the canonical ledger has no downstream_use_case metric anchored to that source/target.",
                        "validation_and_diagnostics.validation_metrics",
                    ))
            elif missing_keys:
                issue_key = ("downstream-siblings", section, _normalize_candidate_target(target_hint), tuple(missing_keys))
                if issue_key not in emitted_prose_errors:
                    emitted_prose_errors.add(issue_key)
                    issues.append(CheckIssue(
                        "error", "SOURCE_DOWNSTREAM_METRIC_SIBLINGS_MISSING",
                        f"A demonstrated/application result in section {section!r} supports additional metric(s) {', '.join(missing_keys)} that are missing from downstream_use_case records.",
                        "validation_and_diagnostics.validation_metrics",
                    ))

    return issues

def _source_item_locator_issues(
    record: FAIRagroApplicationDataFitnessModel,
    bundle: SourceBundle,
) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    registry = getattr(bundle, "source_item_registry", {}) or {}
    if not registry:
        return issues
    payload = record.model_dump(mode="json", by_alias=True, exclude_none=True)

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            iid = value.get("source_item_id")
            if iid and iid not in registry and "claim" in value and "evidence_type" in value:
                issues.append(CheckIssue(
                    "error", "SOURCE_ITEM_ID_UNKNOWN",
                    f"Evidence cites source_item_id={iid!r}, which is absent from the deterministic source-item registry.",
                    path + ".source_item_id",
                ))
            if iid and iid in registry and "claim" in value and "evidence_type" in value:
                meta = registry[iid]
                expected_modalities = {
                    "table": {"author_table"},
                    "figure": {"author_figure", "author_caption"},
                    "formula": {"author_formula"},
                }.get(str(meta.get("item_type") or ""), set())
                modality = str(value.get("source_modality") or "unknown")
                representation = str(value.get("representation_method") or "unknown")
                if modality not in expected_modalities and not (
                    modality == "unknown" and representation in {"docling_structured", "structured_visual_recovery"}
                ):
                    issues.append(CheckIssue(
                        "error", "SOURCE_ITEM_MODALITY_MISMATCH",
                        f"Evidence for {iid} uses source_modality={modality!r}, incompatible with registry item_type={meta.get('item_type')!r}.",
                        path + ".source_modality",
                    ))
                if meta.get("source_page") is not None and value.get("source_page") != meta.get("source_page"):
                    issues.append(CheckIssue(
                        "error", "SOURCE_ITEM_PAGE_MISMATCH",
                        f"Evidence for {iid} has source_page={value.get('source_page')!r}; source registry requires {meta.get('source_page')!r}.",
                        path + ".source_page",
                    ))
                if meta.get("source_location") and value.get("source_location") != meta.get("source_location"):
                    issues.append(CheckIssue(
                        "error", "SOURCE_ITEM_LOCATION_MISMATCH",
                        f"Evidence for {iid} has source_location={value.get('source_location')!r}; source registry requires {meta.get('source_location')!r}.",
                        path + ".source_location",
                    ))
                if meta.get("section_hint") and value.get("source_section") != meta.get("section_hint"):
                    issues.append(CheckIssue(
                        "error", "SOURCE_ITEM_SECTION_MISMATCH",
                        f"Evidence for {iid} has source_section={value.get('source_section')!r}; conservative source-registry mapping is {meta.get('section_hint')!r}.",
                        path + ".source_section",
                    ))
            for k, child in value.items():
                walk(child, f"{path}.{k}")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")

    walk(payload)
    return issues


def _quantitative_value_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    vad = record.validation_and_diagnostics
    groups = [] if vad is None else [
        ("validation_metrics", list(vad.validation_metrics or [])),
        ("tuning_parameters", list(vad.tuning_parameters or [])),
    ]
    for group_name, rows in groups:
        for index, row in enumerate(rows):
            base = f"validation_and_diagnostics.{group_name}[{index}]"
            structured = row.quantitative_value
            display = (row.value_or_summary or "").strip()
            if structured is None:
                if display:
                    issues.append(CheckIssue(
                        "warning", "QUANTITATIVE_VALUE_NOT_STRUCTURED",
                        f"{row.name!r} has a display value but no quantitative_value representation.",
                        base + ".quantitative_value",
                    ))
                continue
            if display and structured.as_reported.strip() != display:
                issues.append(CheckIssue(
                    "error", "QUANTITATIVE_AS_REPORTED_MISMATCH",
                    "quantitative_value.as_reported must exactly preserve value_or_summary.",
                    base + ".quantitative_value.as_reported",
                ))
                continue
            if not display:
                continue
            parsed = parse_quantitative_value(display, metric_name=row.name, unit=row.unit)
            if parsed.kind.value == "text_summary":
                continue
            comparable = ("numeric_value", "lower_bound", "upper_bound")
            mismatches = [
                name for name in comparable
                if getattr(parsed, name) is not None and getattr(structured, name) != getattr(parsed, name)
            ]
            if mismatches:
                issues.append(CheckIssue(
                    "error", "QUANTITATIVE_VALUE_DISPLAY_CONFLICT",
                    f"Structured field(s) {', '.join(mismatches)} conflict with the conservatively parsed display value {display!r}.",
                    base + ".quantitative_value",
                ))
    return issues


def _related_resource_identifier_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    resources = list(record.document_metadata.related_resources or []) if record.document_metadata else []
    for index, resource in enumerate(resources):
        if not resource.identifier:
            continue
        base = f"document_metadata.related_resources[{index}]"
        intended = _enum_value(resource.resource_type) or "other"
        actual = _enum_value(resource.identifier_object_type) or "unknown"
        status = _enum_value(resource.identifier_status) or "unresolved"
        if actual == "article" or (actual not in {"unknown", intended} and intended != "other"):
            issues.append(CheckIssue(
                "error", "RELATED_RESOURCE_IDENTIFIER_OBJECT_MISMATCH",
                f"Identifier for related resource {resource.name!r} resolves to object type {actual!r}, not declared resource type {intended!r}.",
                base + ".identifier_object_type",
            ))
        elif actual == "unknown" or status in {"verification_required", "unresolved"}:
            issues.append(CheckIssue(
                "warning", "RELATED_RESOURCE_IDENTIFIER_REQUIRES_VERIFICATION",
                f"Identifier for related resource {resource.name!r} has not been verified against its identified object type.",
                base + ".identifier_status",
            ))
        if status == "source_explicit" and not resource.evidence:
            issues.append(CheckIssue(
                "error", "EXPLICIT_RESOURCE_IDENTIFIER_LACKS_EVIDENCE",
                f"Related resource {resource.name!r} marks its identifier source_explicit but provides no evidence record.",
                base + ".evidence",
            ))
    return issues


def _beneficiary_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    ap = record.application_profile
    beneficiaries = list(ap.beneficiaries or []) if ap is not None else []
    if not beneficiaries:
        return issues

    if any("(inferred)" in x.lower() for x in beneficiaries):
        issues.append(CheckIssue(
            "error",
            "INFERRED_BENEFICIARY_IN_PLAIN_FIELD",
            "application_profile.beneficiaries is a plain explicit-source field; do not encode '(inferred)' beneficiaries there. "
            "Leave beneficiaries null unless the source explicitly identifies them.",
            "application_profile.beneficiaries",
        ))

    prov = record.extraction_provenance
    inferred_notes = [x.lower() for x in (prov.inferred or [])] if prov is not None else []
    if any("beneficiar" in note and "infer" in note for note in inferred_notes):
        issues.append(CheckIssue(
            "error",
            "BENEFICIARIES_POPULATED_BUT_PROVENANCE_SAYS_INFERRED",
            "Beneficiaries are populated even though extraction provenance identifies them as inferred. "
            "For this schema, leave beneficiaries null unless the source explicitly names/directly identifies them.",
            "application_profile.beneficiaries",
        ))

    return issues

def _flatten_text_values(value: Any) -> list[str]:
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        if value.strip():
            out.append(value.strip())
        return out
    if isinstance(value, (list, tuple, set)):
        for item in value:
            out.extend(_flatten_text_values(item))
        return out
    if hasattr(value, "model_dump"):
        return _flatten_text_values(value.model_dump(mode="python", exclude_none=True))
    if isinstance(value, dict):
        for item in value.values():
            out.extend(_flatten_text_values(item))
    return out


_OVERLAP_STOPWORDS = {
    "a", "an", "and", "or", "the", "of", "for", "to", "from", "with", "in", "on",
    "data", "dataset", "input", "inputs", "source", "sources", "required", "requirement",
    "requirements", "published", "product", "products", "workflow", "producer", "application",
}


def _semantic_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if len(t) > 1 and t not in _OVERLAP_STOPWORDS}


def _text_overlap_score(a: str, b: str) -> float:
    aa = _semantic_tokens(a)
    bb = _semantic_tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def _producer_downstream_overlaps(record: FAIRagroApplicationDataFitnessModel) -> list[tuple[str, str, float]]:
    """Find likely producer/downstream duplication for review.

    This is deliberately only a warning heuristic. Some applications may legitimately
    reuse producer-side inputs, so no values are removed automatically.
    """
    downstream = record.input_data_requirements
    producer = record.producer_workflow_inputs
    if downstream is None or producer is None:
        return []

    downstream_values: list[str] = []
    for field in (
        downstream.data_types,
        downstream.required_sources,
        downstream.spectral_or_variable_requirements,
        downstream.other_requirements,
    ):
        downstream_values.extend(_flatten_text_values(field))

    producer_values: list[str] = []
    for field in (
        producer.data_types,
        producer.sources,
        producer.variables_or_covariates,
        producer.temporal_requirements,
        producer.spatial_requirements,
        producer.method_specific_requirements,
    ):
        producer_values.extend(_flatten_text_values(field))

    matches: list[tuple[str, str, float]] = []
    for d in downstream_values:
        for p in producer_values:
            score = _text_overlap_score(d, p)
            if score >= 0.60:
                matches.append((d, p, score))
                break
    return matches



def _composite_metric_record_issues(record: FAIRagroApplicationDataFitnessModel) -> list[CheckIssue]:
    """Reject canonical records that bundle multiple named metrics into one generic summary."""
    issues: list[CheckIssue] = []
    vad = record.validation_and_diagnostics
    metrics = list(vad.validation_metrics or []) if vad is not None else []
    patterns = {
        "MAE": re.compile(r"\bMAE\b|mean absolute error", re.I),
        "RMSE": re.compile(r"\bRMSE\b|root mean squared error", re.I),
        "MSE": re.compile(r"\bMSE\b|mean squared error", re.I),
        "PICP": re.compile(r"\bPICP\b|prediction[- ]interval coverage", re.I),
        "MPIW": re.compile(r"\bMPIW\b|mean prediction[- ]interval width|mean interval width", re.I),
        "BSE": re.compile(r"\bBSE\b|bayesian posterior standard error", re.I),
    }
    atomic_name = re.compile(
        r"\b(?:MAE|RMSE|MSE|PICP|MPIW|BSE)\b|mean absolute error|root mean squared error|mean squared error|"
        r"prediction[- ]interval coverage|mean prediction[- ]interval width|bayesian posterior standard error",
        re.I,
    )
    for i, metric in enumerate(metrics):
        # A specifically named atomic metric can legitimately cite evidence that mentions siblings.
        if atomic_name.search(metric.name or ""):
            continue
        text = " ".join([metric.name or "", metric.value_or_summary or ""] + [ev.source_text or "" for ev in metric.evidence])
        found = [name for name, rx in patterns.items() if rx.search(text)]
        if len(found) >= 2:
            issues.append(CheckIssue(
                "error", "COMPOSITE_CANONICAL_METRIC_RECORD",
                f"Canonical metric record {metric.name!r} bundles multiple named metrics ({', '.join(found)}). "
                "Keep one metric per canonical record; retain a prose summary as section evidence instead.",
                f"validation_and_diagnostics.validation_metrics[{i}]",
            ))
    return issues


def _provenance_identity_issues(
    record: FAIRagroApplicationDataFitnessModel,
    bundle: SourceBundle,
) -> list[CheckIssue]:
    """Require the standalone record to identify the exact PDF and source bundle."""

    issues: list[CheckIssue] = []
    provenance = record.extraction_provenance
    if provenance is None:
        return [CheckIssue(
            "error",
            "EXTRACTION_PROVENANCE_MISSING",
            "The record lacks extraction_provenance and cannot be bound to its canonical PDF.",
            "extraction_provenance",
        )]
    expected_source = (bundle.source_sha256 or "").lower()
    actual_source = (provenance.canonical_source_sha256 or "").lower()
    expected_source_is_sha = bool(re.fullmatch(r"[0-9a-f]{64}", expected_source))
    if expected_source_is_sha and not actual_source:
        issues.append(CheckIssue(
            "error",
            "CANONICAL_SOURCE_HASH_MISSING",
            "canonical_source_sha256 is required to prove which PDF produced this record.",
            "extraction_provenance.canonical_source_sha256",
        ))
    elif expected_source_is_sha and actual_source != expected_source:
        issues.append(CheckIssue(
            "error",
            "CANONICAL_SOURCE_HASH_MISMATCH",
            "The record canonical_source_sha256 does not match the scientific source package.",
            "extraction_provenance.canonical_source_sha256",
        ))

    expected_bundle = (bundle.bundle_sha256 or "").lower()
    actual_bundle = (provenance.source_bundle_sha256 or "").lower()
    expected_bundle_is_sha = bool(re.fullmatch(r"[0-9a-f]{64}", expected_bundle))
    if expected_bundle_is_sha and not actual_bundle:
        issues.append(CheckIssue(
            "error",
            "SOURCE_BUNDLE_HASH_MISSING",
            "source_bundle_sha256 is required to identify the exact extraction input representation.",
            "extraction_provenance.source_bundle_sha256",
        ))
    elif expected_bundle_is_sha and actual_bundle != expected_bundle:
        issues.append(CheckIssue(
            "error",
            "SOURCE_BUNDLE_HASH_MISMATCH",
            "The record source_bundle_sha256 does not match the bundle used for deterministic checks.",
            "extraction_provenance.source_bundle_sha256",
        ))
    return issues


def _qualitative_guardrail_coverage_issues(
    record: FAIRagroApplicationDataFitnessModel,
    bundle: SourceBundle,
) -> list[CheckIssue]:
    """Flag authored numbered limitations that lack even conservative lexical coverage.

    This is deliberately a warning heuristic: it drives a focused repair/review pass,
    but never invents a limitation or claims semantic equivalence by itself.
    """

    hints = list(getattr(bundle, "qualitative_guardrail_hints", []) or [])
    if not hints:
        return []
    guardrail_values: list[str] = []
    if record.limitations_and_risks is not None:
        limitations = record.limitations_and_risks
        guardrail_values.extend(limitations.known_limitations or [])
        guardrail_values.extend(limitations.risk_of_misuse or [])
        guardrail_values.extend(limitations.bias_sources or [])
        guardrail_values.extend(limitations.extrapolation_limits or [])
    if record.validation_and_diagnostics is not None:
        guardrail_values.extend(record.validation_and_diagnostics.validation_limitations or [])
    if record.decision_risk_profile is not None:
        guardrail_values.extend(record.decision_risk_profile.failure_modes or [])
        guardrail_values.extend(record.decision_risk_profile.consequences_of_misuse or [])

    def stems(text: str) -> set[str]:
        # Light stemming handles common inflection differences without claiming
        # semantic equivalence. Short/common words are excluded from the signal.
        return {token[:7] for token in _semantic_tokens(text) if len(token) >= 5}

    extracted_tokens = stems(" ".join(guardrail_values))
    all_items: list[dict[str, Any]] = []
    for section_index, hint in enumerate(hints):
        items = list(hint.get("numbered_items") or [])
        if not items and hint.get("section_excerpt"):
            items = [{"number": None, "excerpt": hint["section_excerpt"]}]
        all_items.extend({"section_index": section_index, "hint": hint, "item": item} for item in items)
    candidate_token_sets = [stems(str(x["item"].get("excerpt") or "")) for x in all_items]
    document_frequency = Counter(token for tokens in candidate_token_sets for token in tokens)

    issues: list[CheckIssue] = []
    for candidate, candidate_tokens in zip(all_items, candidate_token_sets):
        distinctive_tokens = {token for token in candidate_tokens if document_frequency[token] == 1}
        signal_tokens = distinctive_tokens if len(distinctive_tokens) >= 2 else candidate_tokens
        if len(signal_tokens & extracted_tokens) >= 2:
            continue
        item = candidate["item"]
        hint = candidate["hint"]
        section_index = candidate["section_index"]
        number = item.get("number")
        label = f" item {number}" if number is not None else ""
        issues.append(CheckIssue(
            "warning",
            "QUALITATIVE_GUARDRAIL_CANDIDATE_UNCOVERED",
            f"Authored guardrail section {hint.get('source_section')!r}{label} has low distinctive-term coverage in "
            "limitations/risks/validation limitations. Inspect the source excerpt and preserve every distinct "
            "DFFP-relevant caveat; do not invent a fitness judgment.",
            f"source_bundle.qualitative_guardrail_hints[{section_index}]",
        ))
    return issues

def validate_record(record: FAIRagroApplicationDataFitnessModel, bundle: SourceBundle) -> list[CheckIssue]:
    issues: list[CheckIssue] = []

    dm = record.document_metadata
    if dm and dm.doi:
        doi_text = dm.doi.lower()
        if doi_text.count("doi.org/") > 1 or ";" in doi_text:
            issues.append(CheckIssue(
                "error", "DOCUMENT_DOI_CONFLATION",
                "document_metadata.doi appears to contain multiple identifiers. The field must contain only the source-document DOI.",
                "document_metadata.doi",
            ))

    fc = record.fitness_classification
    if fc and _enum_value(fc.assessment_basis) == "unassessed":
        if fc.suitable_for or fc.conditionally_suitable_for or fc.not_recommended_for:
            issues.append(CheckIssue(
                "error", "UNASSESSED_FITNESS_HAS_CLASSIFICATION",
                "Fitness lists are populated although assessment_basis is unassessed.",
                "fitness_classification",
            ))

    em = record.evidence_and_maturity
    if em and _enum_value(em.assessment_basis) == "unassessed":
        vals = {
            "validation_strength": _enum_value(em.validation_strength),
            "operational_readiness": _enum_value(em.operational_readiness),
            "transferability_risk": _enum_value(em.transferability_risk),
        }
        for name, val in vals.items():
            if val not in {None, "unknown"}:
                issues.append(CheckIssue(
                    "error", "UNASSESSED_MATURITY_HAS_JUDGMENT",
                    f"{name}={val!r} is populated although assessment_basis is unassessed.",
                    f"evidence_and_maturity.{name}",
                ))

    dr = record.decision_risk_profile
    if dr and dr.acceptable_uncertainty_levels and dr.acceptable_uncertainty_is_formal is not True:
        issues.append(CheckIssue(
            "error", "NONFORMAL_UNCERTAINTY_THRESHOLD",
            "acceptable_uncertainty_levels is populated without acceptable_uncertainty_is_formal=true.",
            "decision_risk_profile.acceptable_uncertainty_levels",
        ))

    overlaps = _producer_downstream_overlaps(record)
    if len(overlaps) >= 2:
        examples = "; ".join(f"{d!r} ~ {p!r}" for d, p, _ in overlaps[:3])
        issues.append(CheckIssue(
            "warning", "DOWNSTREAM_PRODUCER_INPUT_OVERLAP",
            "Downstream application requirements substantially overlap producer-workflow inputs. "
            "Verify that input_data_requirements contains only what a downstream user needs after the published dataset exists. "
            f"Examples: {examples}",
            "input_data_requirements",
        ))

    payload_for_terms = record.model_dump(mode="json", by_alias=True, exclude_none=True)
    deprecated_hits = _deprecated_interval_terminology(payload_for_terms)
    if deprecated_hits:
        examples = "; ".join(f"{path}: {text!r}" for path, text in deprecated_hits[:4])
        issues.append(CheckIssue(
            "error",
            "DEPRECATED_INTERVAL_CALIBRATION_TERMINOLOGY",
            "Normalized DFFP fields still use prediction-interval/interval-calibration terminology. "
            "Use prediction-interval assessment, coverage assessment, or coverage-and-width/sharpness wording instead. "
            "Authored source_text is intentionally excluded from this check. "
            f"Examples: {examples}",
            None,
        ))

    issues.extend(_interval_support_issues(record))
    issues.extend(_metric_scope_consistency_issues(record))
    issues.extend(_quantitative_metric_evidence_issues(record))
    issues.extend(_composite_metric_record_issues(record))
    issues.extend(_metric_sibling_split_issues(record))
    issues.extend(_quantitative_value_issues(record))
    issues.extend(_related_resource_identifier_issues(record))
    issues.extend(_provenance_identity_issues(record, bundle))
    issues.extend(_qualitative_guardrail_coverage_issues(record, bundle))
    issues.extend(_source_metric_completeness_issues(record, bundle))
    issues.extend(_source_item_locator_issues(record, bundle))
    issues.extend(_guarded_analysis_type_issues(record))
    issues.extend(_beneficiary_issues(record))

    if bundle.unresolved_items:
        issues.append(CheckIssue(
            "warning", "SOURCE_REPRESENTATION_INCOMPLETE",
            f"The source package still has {len(bundle.unresolved_items)} unresolved/review items. Missing metadata must not be treated as proof of absence from the PDF.",
            None,
        ))

    # Evidence provenance quality checks.
    payload = record.model_dump(mode="json", by_alias=True, exclude_none=True)
    evidence_records: list[dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "claim" in x and "evidence_type" in x and "review_status" in x:
                evidence_records.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(payload)
    explicit_without_locator = 0
    inferred_as_explicit_visual = 0
    for ev in evidence_records:
        if ev.get("evidence_type") == "explicit" and not (
            ev.get("source_text") or ev.get("source_item_id") or ev.get("source_section")
        ):
            explicit_without_locator += 1
        if (
            ev.get("representation_method") == "structured_visual_recovery"
            and ev.get("source_modality") not in {"author_figure", "author_caption", "author_formula", "author_table"}
        ):
            inferred_as_explicit_visual += 1

    if explicit_without_locator:
        issues.append(CheckIssue(
            "warning", "EXPLICIT_EVIDENCE_WEAK_LOCATOR",
            f"{explicit_without_locator} explicit evidence record(s) lack source text/item/section locators.",
            None,
        ))
    if inferred_as_explicit_visual:
        issues.append(CheckIssue(
            "warning", "VISUAL_RECOVERY_MODALITY_MISMATCH",
            f"{inferred_as_explicit_visual} recovered visual evidence record(s) have an unexpected source_modality.",
            None,
        ))

    if not issues:
        issues.append(CheckIssue("info", "NO_DETERMINISTIC_ISSUES", "No configured deterministic semantic checks failed."))
    return issues
