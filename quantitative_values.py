"""Deterministic normalization of display-oriented metric strings.

The source rendering remains in ``value_or_summary`` and ``as_reported``.  This
module only parses conservative, unambiguous numeric shapes; it never invents a
confidence level, unit, aggregation, or meaning that is not present.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Optional

from models import (
    AggregationStatistic,
    FAIRagroApplicationDataFitnessModel,
    MetricValueKind,
    QuantitativeMetricValue,
    UncertaintyComponent,
    UncertaintyScale,
)

_NUM = r"[+-]?(?:\d+(?:[.,]\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_ESTIMATE_RANGE = re.compile(
    rf"^\s*(?P<value>{_NUM})\s*[\(\[]\s*(?P<lower>{_NUM})\s*(?:[-–—]|\bto\b)\s*"
    rf"(?P<upper>{_NUM})\s*[\)\]]\s*(?:%|percent)?\s*$",
    re.I,
)
_RANGE_WITH_NAMED_ESTIMATE = re.compile(
    rf"^\s*(?P<lower>{_NUM})\s*(?:[-–—]|\bto\b)\s*(?P<upper>{_NUM})"
    rf"(?:\s+[A-Za-z°%][A-Za-z0-9°%./_-]*)?\s*(?:[;,]|\(|\[)?\s*"
    rf"(?P<aggregation>median|mean)\s*[:=]?\s*(?P<value>{_NUM})"
    rf"(?:\s+[A-Za-z°%][A-Za-z0-9°%./_-]*)?\s*[\)\]]?\s*$",
    re.I,
)
_NAMED_ESTIMATE_WITH_RANGE = re.compile(
    rf"^\s*(?P<aggregation>median|mean)\s*[:=]?\s*(?P<value>{_NUM})"
    rf"(?:\s+[A-Za-z°%][A-Za-z0-9°%./_-]*)?\s*(?:[;,]|\(|\[)?\s*"
    rf"(?:range|min[- ]max)\s*[:=]?\s*(?P<lower>{_NUM})\s*(?:[-–—]|\bto\b)\s*"
    rf"(?P<upper>{_NUM})(?:\s+[A-Za-z°%][A-Za-z0-9°%./_-]*)?\s*[\)\]]?\s*$",
    re.I,
)
_RANGE = re.compile(
    rf"^\s*(?P<lower>{_NUM})\s*(?:[-–—]|\bto\b)\s*(?P<upper>{_NUM})\s*(?:%|percent)?\s*$",
    re.I,
)
_PLUS_MINUS = re.compile(
    rf"^\s*(?P<value>{_NUM})\s*(?:±|\+/-)\s*(?P<uncertainty>{_NUM})\s*(?:%|percent)?\s*$",
    re.I,
)
_SCALAR = re.compile(rf"^\s*(?P<value>{_NUM})\s*(?P<percent>%|percent)?\s*$", re.I)
_NOMINAL_LEVEL = re.compile(r"\b(?P<level>\d{1,3}(?:\.\d+)?)\s*%\s*(?:CI|confidence|PI|prediction)", re.I)

_UCUM_UNITS = {
    "%": "%",
    "percent": "%",
    "percentage": "%",
    "d": "d",
    "day": "d",
    "days": "d",
    "h": "h",
    "hour": "h",
    "hours": "h",
    "a": "a",
    "yr": "a",
    "year": "a",
    "years": "a",
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "km": "km",
    "g": "g",
    "kg": "kg",
    "t": "t",
    "°c": "Cel",
    "degrees c": "Cel",
}


def _decimal(raw: str) -> Decimal:
    value = raw.strip()
    if "," in value and "." not in value:
        value = value.replace(",", ".")
    try:
        return Decimal(value)
    except InvalidOperation as exc:  # guarded by the regular expressions above
        raise ValueError(f"Invalid decimal value: {raw!r}") from exc


def _unit_code(unit: Optional[str], as_reported: str) -> Optional[str]:
    normalized = (unit or "").strip().lower()
    if normalized in _UCUM_UNITS:
        return _UCUM_UNITS[normalized]
    if not normalized and re.search(r"(?:%|\bpercent\b)\s*$", as_reported, re.I):
        return "%"
    return None


def _aggregation(metric_name: str, as_reported: str) -> AggregationStatistic:
    text = f"{metric_name} {as_reported}".lower()
    for token, value in (
        ("median", AggregationStatistic.median),
        ("mean", AggregationStatistic.mean),
        ("minimum", AggregationStatistic.minimum),
        ("maximum", AggregationStatistic.maximum),
        ("count", AggregationStatistic.count),
    ):
        if re.search(rf"\b{token}\b", text):
            return value
    if re.search(r"(?:%|\bpercent(?:age)?\b|\bproportion\b)", text):
        return AggregationStatistic.proportion
    return AggregationStatistic.unspecified


def parse_quantitative_value(
    as_reported: str,
    *,
    metric_name: str = "",
    unit: Optional[str] = None,
) -> QuantitativeMetricValue:
    """Parse a conservative set of numeric representations.

    Unrecognized strings become ``text_summary`` rather than being guessed.  A
    later human or domain-specific step may enrich them explicitly.
    """

    text = as_reported.strip()
    common = {
        "as_reported": text,
        "unit_code": _unit_code(unit, text),
        "aggregation": _aggregation(metric_name, text),
    }

    # A reported central statistic and its range are complementary, not competing
    # representations. Preserve both, regardless of whether the paper writes the
    # range or the central statistic first (for example, ``2.8-5.1 days (median
    # 3.9 days)``).
    for pattern in (_RANGE_WITH_NAMED_ESTIMATE, _NAMED_ESTIMATE_WITH_RANGE):
        match = pattern.fullmatch(text)
        if match:
            common["aggregation"] = AggregationStatistic(match.group("aggregation").lower())
            return QuantitativeMetricValue(
                kind=MetricValueKind.estimate_with_range,
                numeric_value=_decimal(match.group("value")),
                lower_bound=_decimal(match.group("lower")),
                upper_bound=_decimal(match.group("upper")),
                range_basis="reported min-max or across-group range",
                **common,
            )

    match = _ESTIMATE_RANGE.fullmatch(text)
    if match:
        return QuantitativeMetricValue(
            kind=MetricValueKind.estimate_with_range,
            numeric_value=_decimal(match.group("value")),
            lower_bound=_decimal(match.group("lower")),
            upper_bound=_decimal(match.group("upper")),
            range_basis="reported interval or range (type not stated)",
            **common,
        )

    match = _PLUS_MINUS.fullmatch(text)
    if match:
        return QuantitativeMetricValue(
            kind=MetricValueKind.estimate_with_uncertainty,
            numeric_value=_decimal(match.group("value")),
            uncertainty_components=[
                UncertaintyComponent(
                    name="reported plus-minus uncertainty",
                    value=abs(_decimal(match.group("uncertainty"))),
                    unit_code=common["unit_code"],
                    scale=UncertaintyScale.unknown,
                )
            ],
            **common,
        )

    match = _RANGE.fullmatch(text)
    if match:
        return QuantitativeMetricValue(
            kind=MetricValueKind.range,
            lower_bound=_decimal(match.group("lower")),
            upper_bound=_decimal(match.group("upper")),
            range_basis="reported range (basis not stated)",
            **common,
        )

    match = _SCALAR.fullmatch(text)
    if match:
        return QuantitativeMetricValue(
            kind=MetricValueKind.scalar,
            numeric_value=_decimal(match.group("value")),
            **common,
        )

    nominal_match = _NOMINAL_LEVEL.search(text)
    nominal_level = None
    if nominal_match:
        level = _decimal(nominal_match.group("level")) / Decimal("100")
        nominal_level = level if Decimal("0") <= level <= Decimal("1") else None
    return QuantitativeMetricValue(
        kind=MetricValueKind.text_summary,
        nominal_level=nominal_level,
        **common,
    )


def enrich_quantitative_values(
    record: FAIRagroApplicationDataFitnessModel,
) -> FAIRagroApplicationDataFitnessModel:
    """Backfill structured values without overwriting explicit model/human data."""

    diagnostics = record.validation_and_diagnostics
    if diagnostics is None:
        return record
    rows = list(diagnostics.validation_metrics or []) + list(diagnostics.tuning_parameters or [])
    for row in rows:
        if not row.value_or_summary or not row.value_or_summary.strip():
            continue
        if row.quantitative_value is not None:
            _enrich_existing_range_with_reported_central_value(row)
            continue
        row.quantitative_value = parse_quantitative_value(
            row.value_or_summary,
            metric_name=row.name,
            unit=row.unit,
        )
    return record


def _enrich_existing_range_with_reported_central_value(row: Any) -> None:
    """Recover a reported median/mean that a model omitted from a range display.

    The deterministic upgrade is deliberately narrow: the same text fragment must
    contain the existing lower/upper bounds and the named central statistic. This
    prevents a median from a neighbouring metric or target being borrowed.
    """

    structured = row.quantitative_value
    if structured is None:
        return
    if structured.kind == MetricValueKind.text_summary:
        parsed = parse_quantitative_value(row.value_or_summary, metric_name=row.name, unit=row.unit)
        if parsed.kind != MetricValueKind.text_summary:
            row.quantitative_value = parsed
        return
    if structured.kind != MetricValueKind.range:
        return
    if structured.lower_bound is None or structured.upper_bound is None:
        return

    # First handle a complete display paired with an incorrectly shaped object.
    parsed = parse_quantitative_value(row.value_or_summary, metric_name=row.name, unit=row.unit)
    if parsed.kind == MetricValueKind.estimate_with_range:
        parsed.range_basis = structured.range_basis or parsed.range_basis
        row.quantitative_value = parsed
        return

    fragments = [getattr(row, "interpretation", None)]
    for evidence in list(getattr(row, "evidence", None) or []):
        fragments.extend([getattr(evidence, "source_text", None), getattr(evidence, "claim", None)])
    lower = structured.lower_bound
    upper = structured.upper_bound
    for fragment in (str(value) for value in fragments if value):
        for pattern in (_RANGE_WITH_NAMED_ESTIMATE, _NAMED_ESTIMATE_WITH_RANGE):
            # The public parser patterns are anchored. Search each sentence/claim
            # independently so surrounding prose cannot change the numeric match.
            for sentence in re.split(r"(?<=[.!?])\s+", fragment):
                candidate = sentence.strip().strip(".,;:")
                match = pattern.search(candidate)
                if match is None:
                    # Allow prose before the metric phrase by searching a bounded
                    # substring beginning at the existing lower bound or statistic.
                    starts = [m.start() for m in re.finditer(rf"(?:median|mean|{re.escape(str(lower))})", candidate, re.I)]
                    for start in starts:
                        match = pattern.fullmatch(candidate[start:].strip().strip(".,;:"))
                        if match is not None:
                            break
                if match is None:
                    continue
                if _decimal(match.group("lower")) != lower or _decimal(match.group("upper")) != upper:
                    continue
                central_raw = match.group("value")
                aggregation = match.group("aggregation").lower()
                display = f"{row.value_or_summary.strip()} ({aggregation} {central_raw})"
                row.value_or_summary = display
                structured.kind = MetricValueKind.estimate_with_range
                structured.as_reported = display
                structured.numeric_value = _decimal(central_raw)
                structured.aggregation = AggregationStatistic(aggregation)
                return
