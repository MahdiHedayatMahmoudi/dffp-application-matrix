"""Build a provenance-aware LLM source bundle from a scientific source package.

The canonical source remains the PDF. Clean Docling Markdown is the primary
readable prose channel. Structured tables, formulas and recovered figures are
added as explicitly typed evidence channels. Derived picture descriptions are
never mixed into authored prose.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from settings import Settings, get_settings


@dataclass(frozen=True)
class SourceBundle:
    package_dir: str
    source_filename: str
    source_sha256: str
    source_fidelity_status: str
    source_fidelity_initial_status: str
    source_fidelity_summary: dict[str, Any]
    unresolved_items: list[dict[str, Any]]
    clean_markdown_path: str
    text_for_llm: str
    bundle_sha256: str
    item_counts: dict[str, int]
    source_item_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    metric_candidate_hints: list[dict[str, Any]] = field(default_factory=list)
    metric_candidate_focus: list[dict[str, Any]] = field(default_factory=list)
    qualitative_guardrail_hints: list[dict[str, Any]] = field(default_factory=list)

    def manifest_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("text_for_llm", None)
        return data


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8", errors="replace")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _trim(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[TRUNCATED {len(text)-limit} CHARACTERS]..."


def _json_block(obj: Any, limit: int) -> str:
    return _trim(json.dumps(obj, ensure_ascii=False, indent=2), limit)



# Common metric aliases are used only to normalize names, never to inject values.
# The candidate system also supports unfamiliar paper-specific metric names through
# generic metric-like table headers and name/value prose patterns.
_KNOWN_METRIC_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MAE", re.compile(r"^(?:MAE|mean absolute error)$", re.I)),
    ("RMSE", re.compile(r"^(?:RMSE|root mean squared error)$", re.I)),
    ("MSE", re.compile(r"^(?:MSE|mean squared error)$", re.I)),
    ("R2", re.compile(r"^(?:R2|R\^2|R²|coefficient of determination)$", re.I)),
    ("PICP", re.compile(r"^(?:PICP|prediction[- ]interval coverage(?: probability)?)$", re.I)),
    ("MPIW", re.compile(r"^(?:MPIW|mean prediction[- ]interval width|mean interval width)$", re.I)),
    ("BSE", re.compile(r"^(?:BSE|Bayesian posterior standard error)$", re.I)),
    ("accuracy", re.compile(r"^(?:accuracy|overall accuracy)$", re.I)),
    ("precision", re.compile(r"^precision$", re.I)),
    ("recall", re.compile(r"^(?:recall|sensitivity)$", re.I)),
    ("F1", re.compile(r"^(?:F1|F1[- ]score)$", re.I)),
    ("AUC", re.compile(r"^(?:AUC|AUROC|area under (?:the )?(?:ROC )?curve)$", re.I)),
    ("bias", re.compile(r"^(?:bias|mean bias)$", re.I)),
    ("correlation", re.compile(r"^(?:correlation|Pearson(?:'s)? r|Spearman(?:'s)? rho)$", re.I)),
    ("NSE", re.compile(r"^(?:NSE|Nash[- ]Sutcliffe efficiency)$", re.I)),
    ("KGE", re.compile(r"^(?:KGE|Kling[- ]Gupta efficiency)$", re.I)),
    ("p_value", re.compile(r"^(?:p[- ]?value|p)$", re.I)),
    ("standard_error", re.compile(r"^(?:standard error|SE)$", re.I)),
    ("standard_deviation", re.compile(r"^(?:standard deviation|SD)$", re.I)),
    ("coefficient_of_variation", re.compile(r"^(?:coefficient of variation|CV)$", re.I)),
)

_METRIC_HEADER_TERMS = re.compile(
    r"(?:error|accuracy|precision|recall|sensitivity|specificity|score|auc|roc|bias|"
    r"correlation|coverage|interval width|uncertaint|standard error|standard deviation|"
    r"coefficient of variation|reliab|sharpness|fit|likelihood|deviance|efficiency|"
    r"rmse|mae|mse|mape|nse|kge|picp|mpiw|bse|r\^?2|r²|p[- ]?value)",
    re.I,
)

_NON_METRIC_HEADER = re.compile(
    r"^(?:id|identifier|name|year|date|time|phase|class|category|treatment|site|station|"
    r"region|country|sample|n|count|number|doi|url|version)$",
    re.I,
)


def _portable_relpath(value: str | None) -> Path:
    return Path(str(value or "").replace("\\", "/"))


_GUARDRAIL_HEADING = re.compile(r"\b(?:limitations?|caveats?|risks?)\b", re.I)


def _build_qualitative_guardrail_hints(clean_md: str) -> list[dict[str, Any]]:
    """Expose authored limitation/risk sections as a compact omission checklist.

    This does not classify or rewrite the prose. It only makes high-value authored
    guardrails easier for the extractor to review, which reduces the chance that a
    numerically rich record silently drops conditions on interpretation and reuse.
    """

    lines = clean_md.splitlines()
    headings: list[tuple[int, int, str]] = []
    for position, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((position, len(match.group(1)), _clean_heading(match.group(2))))

    hints: list[dict[str, Any]] = []
    for heading_index, (start, level, title) in enumerate(headings):
        if not _GUARDRAIL_HEADING.search(title):
            continue
        end = len(lines)
        for next_start, next_level, _ in headings[heading_index + 1:]:
            if next_level <= level:
                end = next_start
                break
        body_lines = [
            line.strip()
            for line in lines[start + 1:end]
            if line.strip() and not re.fullmatch(r"\d{1,4}", line.strip())
        ]
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        numbered_items: list[dict[str, Any]] = []
        matches = list(re.finditer(r"(?m)^(?P<number>\d+)\.\s+", body))
        for item_index, match in enumerate(matches):
            item_end = matches[item_index + 1].start() if item_index + 1 < len(matches) else len(body)
            item_text = body[match.end():item_end].strip()
            if item_text:
                numbered_items.append({
                    "number": int(match.group("number")),
                    "excerpt": _trim(item_text, 5000),
                })
        hints.append({
            "candidate_id": hashlib.sha256(f"{title}|{body}".encode("utf-8")).hexdigest()[:16],
            "source_section": title,
            "numbered_items": numbered_items,
            "section_excerpt": _trim(body, 12000),
        })
    return hints


def _clean_heading(title: str) -> str:
    """Remove obvious PDF line-number serialization noise without rewriting titles."""
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    # Numbered manuscript headings sometimes acquire a trailing line number, e.g.
    # ``2 Methods 130``. Strip only non-year trailing integers from numbered headings.
    m = re.match(r"^(\d+(?:\.\d+)*)\s+(.+?)\s+(\d{2,4})$", text)
    if m:
        tail = int(m.group(3))
        if not (1900 <= tail <= 2100):
            return f"{m.group(1)} {m.group(2).strip()}"
    return text


def _markdown_heading_context(clean_md: str) -> tuple[list[str], list[tuple[int, str]]]:
    lines = clean_md.splitlines()
    headings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if m:
            headings.append((i, _clean_heading(m.group(1))))
    return lines, headings


def _nearest_heading_before(line_no: int, headings: list[tuple[int, str]]) -> str | None:
    prior = [title for pos, title in headings if pos < line_no]
    return prior[-1] if prior else None


def _section_hint_for_label(clean_md: str, labels: list[str]) -> str | None:
    """Return a conservative section hint from a non-caption textual reference."""
    lines, headings = _markdown_heading_context(clean_md)
    if not labels:
        return None
    escaped = [re.escape(x) for x in labels if x]
    if not escaped:
        return None
    rx = re.compile(r"(?:" + "|".join(escaped) + r")", re.I)
    caption_start = re.compile(r"^\s*(?:Table|Figure|Fig\.|Eq\.|Equation)\s+", re.I)
    for i, line in enumerate(lines):
        if caption_start.search(line):
            continue
        if rx.search(line):
            hint = _nearest_heading_before(i, headings)
            if hint:
                return hint
    return None


def _build_source_item_registry(fidelity: dict[str, Any], clean_md: str) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for table in fidelity.get("tables", []) or []:
        iid = str(table.get("table_id") or "")
        if not iid:
            continue
        num = str(table.get("table_number") or "").strip()
        location = f"Table {num}" if num else iid
        registry[iid] = {
            "item_type": "table",
            "source_location": location,
            "source_page": table.get("page_no"),
            "section_hint": _section_hint_for_label(clean_md, [location]),
            "caption": table.get("caption"),
        }
    for fig in fidelity.get("figures", []) or []:
        iid = str(fig.get("figure_id") or "")
        if not iid:
            continue
        num = str(fig.get("figure_number") or fig.get("source_figure_number") or "").strip()
        location = f"Figure {num}" if num else iid
        labels = [location, f"Fig. {num}" if num else ""]
        registry[iid] = {
            "item_type": "figure",
            "source_location": location,
            "source_page": fig.get("page_no"),
            "section_hint": _section_hint_for_label(clean_md, labels),
            "caption": fig.get("caption") or fig.get("source_caption"),
        }
    for formula in fidelity.get("formulas", []) or []:
        iid = str(formula.get("formula_id") or "")
        if not iid:
            continue
        try:
            num = int(iid.rsplit("_", 1)[-1])
        except Exception:
            num = None
        location = f"Eq. {num}" if num is not None else iid
        labels = [location, f"Equation {num}" if num is not None else ""]
        registry[iid] = {
            "item_type": "formula",
            "source_location": location,
            "source_page": formula.get("page_no"),
            "section_hint": _section_hint_for_label(clean_md, labels),
            "caption": None,
        }
    return registry


def _known_metric_name(text: str) -> str | None:
    value = str(text or "").strip()
    for name, pattern in _KNOWN_METRIC_ALIASES:
        if pattern.match(value):
            return name
    return None


def _metric_key(text: str) -> str:
    known = _known_metric_name(text)
    if known:
        return known.lower()
    value = re.sub(r"\([^)]*\)", " ", str(text or "").lower())
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value[:80]


def _metric_like_header(column: str) -> bool:
    text = str(column or "").strip()
    if not text or _NON_METRIC_HEADER.match(text):
        return False
    return bool(_known_metric_name(text) or _METRIC_HEADER_TERMS.search(text))


def _looks_numeric(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and re.search(r"[-+]?\d+(?:[.,]\d+)?", text))


def _best_row_target(row: dict[str, Any], metric_columns: set[str]) -> str | None:
    candidates: list[tuple[str, str]] = []
    for key, value in row.items():
        if key in metric_columns:
            continue
        text = str(value or "").strip()
        if not text or text == "-":
            continue
        candidates.append((str(key), text))
    # Prefer a descriptive non-numeric label over year/count identifiers.
    for key, text in candidates:
        if not _looks_numeric(text) and not _NON_METRIC_HEADER.match(key):
            return text
    for key, text in candidates:
        if not _looks_numeric(text):
            return text
    return candidates[0][1] if candidates else None


def _candidate_id(*parts: str) -> str:
    raw = "\n".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _table_metric_candidates(package: Path, fidelity: dict[str, Any], registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Create generic metric-like table hints without assigning fitness or scope."""
    out: list[dict[str, Any]] = []
    for table in fidelity.get("tables", []) or []:
        iid = str(table.get("table_id") or "")
        folder = package / _portable_relpath(str(table.get("folder") or f"tables/{iid}"))
        records = _read_json(folder / "table.records.json", []) or []
        if not isinstance(records, list):
            continue
        caption = str(table.get("caption") or "")
        for row_index, row in enumerate(records):
            if not isinstance(row, dict):
                continue
            # Preserve authored column order for deterministic bundle hashes and prompts.
            metric_columns = [str(k) for k in row.keys() if _metric_like_header(str(k))]
            if not metric_columns:
                continue
            target = _best_row_target(row, set(metric_columns))
            for column in metric_columns:
                value = row.get(column)
                if not _looks_numeric(value):
                    continue
                meta = registry.get(iid, {})
                metric_name = _known_metric_name(column) or str(column).strip()
                out.append({
                    "kind": "structured_table_metric",
                    "candidate_id": _candidate_id(iid, str(row_index), column, str(value)),
                    "metric_name": metric_name,
                    "metric_key": _metric_key(metric_name),
                    "metric_family": _known_metric_name(column),  # backwards-compatible hint
                    "target": target,
                    "value": str(value).strip(),
                    "column": str(column),
                    "row_index": row_index,
                    "source_item_id": iid,
                    "source_page": meta.get("source_page"),
                    "source_location": meta.get("source_location"),
                    "section_hint": meta.get("section_hint"),
                    "caption": _trim(caption, 500) if caption else None,
                })
    return out


def _valid_target_hint(hint: str | None) -> str | None:
    """Reject method/citation fragments that are not plausible scientific targets."""
    hint = re.sub(r"\s+", " ", (hint or "")).strip(" ,;:.-")
    if not hint or not (1 <= len(hint.split()) <= 12):
        return None
    low = hint.lower()
    if re.search(r"\bet\s+al\.?\b", low):
        return None
    if re.match(r"^(?:validat|evaluat|assess|estimat|calibrat|follow|using|use|comput|deriv|obtain)\w*\b", low):
        return None
    if re.search(r"\b(?:protocol|procedure|method|approach)\s+for\b", low):
        return None
    # Citation-like parentheticals with author conjunctions are not targets.
    if "(" in hint and re.search(r"\band\b", low):
        return None
    return hint


def _extract_target_hint(text: str) -> str | None:
    # Conservative grammatical cues only; this is a discovery hint, not a schema value.
    m = re.search(r"\b(?:for|in|among|across)\s+(?:the\s+)?([^,;:.]{2,80})[,;:]", text, re.I)
    if m:
        valid = _valid_target_hint(m.group(1))
        if valid:
            return valid
    # Subject + result verb, e.g. "Method A achieved ..." or "Heading has an MAE ...".
    m = re.search(
        r"(?:^|[.!?]\s+)(?:the\s+)?([A-Za-z][A-Za-z0-9 _/-]{1,60}?)\s+"
        r"(?:has|had|shows?|showed|achieves?|achieved|yields?|yielded|gives?|gave|reports?|reported)\b",
        text, re.I,
    )
    if m:
        valid = _valid_target_hint(m.group(1))
        if valid:
            return valid
    # Passive/copular scientific result phrasing.
    m = re.search(
        r"(?:^|[.!?]\s+)(?:the\s+)?([A-Za-z][A-Za-z0-9 _/-]{1,60}?)(?:\s*\([^)]{1,30}\))?\s+"
        r"(?:is|was|are|were)\s+[^.;]{0,90}?(?:,\s*with\b|\bwith\s+(?:an?|the)\b)",
        text, re.I,
    )
    if m:
        valid = _valid_target_hint(m.group(1))
        if valid:
            return valid
    return None


def _extract_quantitative_pairs(text: str) -> list[dict[str, str]]:
    """Extract conservative explicit metric/value pairs from prose.

    Completeness checks should prefer false negatives over false positives: a metric name
    merely occurring near a section number, file identifier, nominal level, citation, or
    enumerated method step is not a quantitative result.  Common metrics therefore require
    an adjacent value or an explicit result relation (``= ``, ``is``, ``median``, ``spans``,
    ``increases from`` ...).  Paper-specific abbreviations remain supported through explicit
    name/value operators.
    """
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    scalar = r"[-+]?\d+(?:[.,]\d+)?"
    number = rf"{scalar}(?:\s*[-–]\s*{scalar})?(?:\s*(?:±|\+/-)\s*{scalar})?"
    unit = r"(?:%|[A-Za-zµμ°][A-Za-z0-9µμ°/^·._-]{0,15})?"

    def add(name: str, value: str, unit_value: str | None = None, *, role: str = "result") -> None:
        key = _metric_key(name)
        item = (key, value)
        if item in seen:
            return
        seen.add(item)
        row = {"metric_name": name, "metric_key": key, "value": value, "candidate_role": role}
        if unit_value:
            row["unit_hint"] = unit_value.strip()
        pairs.append(row)

    for name, pattern in _KNOWN_METRIC_ALIASES:
        raw = pattern.pattern.removeprefix("^").removesuffix("$")
        metric = rf"(?<![A-Za-z0-9])(?:{raw})(?![A-Za-z0-9])"
        # Adjacent result: ``MAE 5.7`` or ``PICP: 0.90``.
        direct = re.compile(rf"{metric}\s*(?:=|:)?\s*({number})\s*({unit})", re.I)
        # Explicit relation: ``RMSE of 6.5``, ``BSE increases from 0.76``,
        # ``MAE spans 4.6-5.7`` or ``PICP is 0.90``.
        relation = re.compile(
            rf"{metric}\s{{0,12}}(?:value(?:s)?\s+)?"
            rf"(?:is|are|was|were|of|about|approximately|equals?|=|:|spans?|ranges?\s+from|"
            rf"increases?\s+from|decreases?\s+from|rises?\s+from|falls?\s+from)\s*"
            rf"({number})\s*({unit})", re.I
        )
        # Parenthetical summary: ``RMSE; median 7.1 days``.
        summary = re.compile(rf"{metric}\s*[;,]\s*(?:mean|median|range)\s*({number})\s*({unit})", re.I)
        # Result range after a nominal/method phrase: ``PICP ... lies between 0.896 and 0.901``.
        between = re.compile(rf"{metric}[^.;\n]{{0,90}}?\b(?:lies?|ranges?)\s+between\s+({scalar})\s+(?:and|[-–])\s+({scalar})\s*({unit})", re.I)

        for rx in (direct, relation, summary):
            for m in rx.finditer(text):
                # Direct matches must actually be adjacent; reject identifier-like bridges
                # such as ``RMSE ... VAM_202`` and nominal-level statements.
                local = text[max(0, m.start()-20):m.end()+20]
                unit_value = (m.group(2) or "").strip()
                # Formula exponents/indices such as ``SE^2_i`` are not reported metric
                # values. PDF serialization often flattens them to ``SE 2 i``.
                if name in {"standard_error", "standard_deviation"} and re.fullmatch(r"[-+]?\d+", m.group(1)) and re.fullmatch(r"[A-Za-z]", unit_value or ""):
                    if re.search(r"[√σ^]|\bz\b|\bprod\b|\bwhere\b", local, re.I):
                        continue
                if re.search(r"\b(?:file|series|table|fig(?:ure)?|eq(?:uation)?|section|sect\.)\b[^.;]{0,25}$", text[max(0,m.start()-45):m.start()], re.I):
                    continue
                if re.search(r"\bnominal\s*$", text[max(0,m.start()-30):m.start()], re.I):
                    continue
                add(name, m.group(1), m.group(2) or None)
        for m in between.finditer(text):
            add(name, f"{m.group(1)}-{m.group(2)}", m.group(3) or None)

    # Paper-specific abbreviations such as custom indices require an explicit operator.
    for m in re.finditer(rf"\b([A-Z][A-Z0-9_-]{{1,15}})\b\s*(?:=|:)\s*({number})\s*({unit})", text):
        add(m.group(1), m.group(2), m.group(3) or None)

    # Descriptive metric phrases also require an explicit operator.
    for m in re.finditer(
        rf"\b([A-Za-z][A-Za-z -]{{2,55}}?(?:error|accuracy|bias|coverage|width|precision|uncertainty|score|coefficient|efficiency))\s*(?:=|:)\s*({number})\s*({unit})",
        text, re.I,
    ):
        add(re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2), m.group(3) or None)
    return pairs


def _sentence_like_windows(text: str) -> list[str]:
    """Return compact sentence-like windows while preserving decimal numbers.

    Scientific PDFs often serialize one semantic sentence across several visual lines.  A
    paragraph-only candidate can become too large for reliable metric/target association;
    sentence windows provide a second, more focused view without changing the source text.
    """
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return []
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", compact)
    pieces = [x.strip() for x in pieces if x.strip()]
    windows: list[str] = []
    for i, piece in enumerate(pieces):
        windows.append(piece)
        # Pair adjacent sentences because scientific prose often introduces a metric in one
        # sentence and reports its value in the next (e.g. 'MPIW denotes width. For X, mean
        # interval width is ...').
        if i + 1 < len(pieces):
            joined = f"{piece} {pieces[i+1]}"
            if len(joined) <= 1800:
                windows.append(joined)
    return windows


_RELEVANT_QUALITY_SECTION = re.compile(
    r"validat|evaluat|accuracy|performance|uncertaint|error|diagnostic|quality|reliab|precision|assessment|calibrat",
    re.I,
)
_DOWNSTREAM_SECTION = re.compile(
    r"use[ -]?case|case study|demonstrat|application|worked example|example application|proof of concept|downstream",
    re.I,
)
_SUMMARY_SIGNAL = re.compile(
    r"\b(?:mean|median|average|range|overall|pooled|family[- ]wide|across|summary|trend|increas|decreas|declin|rises?|falls?)\b|±|\+/-",
    re.I,
)


def _prose_metric_candidates(clean_md: str, limit: int = 220) -> list[dict[str, Any]]:
    """Discover quantitative quality/use-case statements from authored prose.

    Candidates are generic source hints.  They never decide fitness, target scope, or
    acceptance thresholds.  Both paragraph and sentence-window views are considered so
    quantitative results in prose/captions are not systematically disadvantaged relative
    to structured tables.
    """
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    lines = clean_md.splitlines()
    current_section: str | None = None
    buf: list[str] = []

    def emit(text: str, section: str | None) -> None:
        if len(out) >= limit:
            return
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text or not re.search(r"\d", text):
            return
        if section and re.search(r"^(?:references|bibliography|literature cited)\b", section, re.I):
            return
        pairs = _extract_quantitative_pairs(text)
        relevant_quality = bool(section and _RELEVANT_QUALITY_SECTION.search(section))
        downstream = bool(section and _DOWNSTREAM_SECTION.search(section))
        uncertainty_result = bool(re.search(r"[-+]?\d+(?:[.,]\d+)?\s*(?:±|\+/-)\s*\d+(?:[.,]\d+)?", text))
        if not pairs and not (downstream and uncertainty_result):
            return
        cid = _candidate_id(section or "", text)
        if cid in seen_ids:
            return
        seen_ids.add(cid)
        target_hint = _extract_target_hint(text)
        summary_signal = bool(_SUMMARY_SIGNAL.search(text))
        downstream_result = downstream and bool(
            uncertainty_result
            or (pairs and (summary_signal or re.search(r"\b(?:result|indicator|metric|score|index|estimate|value|yield(?:ing|ed)?)\b", text, re.I)))
        )
        out.append({
            "kind": "prose_metric_candidate",
            "candidate_id": cid,
            "metric_names": [x["metric_name"] for x in pairs],
            "metric_keys": [x["metric_key"] for x in pairs],
            "result_metric_keys": [x["metric_key"] for x in pairs if x.get("candidate_role") == "result"],
            "metric_families": [x["metric_name"] for x in pairs if _known_metric_name(x["metric_name"])],
            "quantitative_pairs": pairs,
            "target_hint": target_hint,
            "summary_signal": summary_signal,
            "quality_context": relevant_quality,
            "downstream_use_case_result": downstream_result,
            "source_section": section,
            "excerpt": _trim(text, 1400),
        })

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        paragraph = " ".join(x.strip() for x in buf if x.strip()).strip()
        buf = []
        section = _clean_heading(current_section or "") or None
        emit(paragraph, section)
        for window in _sentence_like_windows(paragraph):
            if window != paragraph:
                emit(window, section)

    for line in lines:
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if m:
            flush()
            current_section = _clean_heading(m.group(1))
            continue
        if not line.strip():
            flush()
        else:
            buf.append(line)
    flush()
    return out


def _metric_candidate_focus(hints: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    """Create a compact, high-attention completeness view from current-source hints.

    This is deliberately document-agnostic.  It prioritizes explicit downstream numerical
    results and summary-level validation/quality/uncertainty prose, then selected structured
    rows with several metric-like siblings.  No metric values or domain entities are coded
    into the prioritization rules.
    """
    focus: list[dict[str, Any]] = []
    # High-value prose first.
    for h in hints:
        if h.get("kind") != "prose_metric_candidate":
            continue
        if not (h.get("downstream_use_case_result") or (h.get("quality_context") and h.get("summary_signal"))):
            continue
        focus.append({
            "candidate_id": h.get("candidate_id"),
            "kind": h.get("kind"),
            "source_section": h.get("source_section"),
            "target_hint": h.get("target_hint"),
            "quantitative_pairs": h.get("quantitative_pairs") or [],
            "downstream_use_case_result": bool(h.get("downstream_use_case_result")),
            "excerpt": h.get("excerpt"),
        })
        if len(focus) >= limit:
            return focus

    # Compact table sibling groups next.
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for h in hints:
        if h.get("kind") != "structured_table_metric":
            continue
        key = (str(h.get("source_item_id") or ""), str(h.get("target") or ""))
        grouped.setdefault(key, []).append(h)
    for (iid, target), rows in grouped.items():
        if len(rows) < 2:
            continue
        focus.append({
            "kind": "structured_table_metric_group",
            "source_item_id": iid,
            "target": target,
            "metrics": [
                {"metric_name": r.get("metric_name"), "metric_key": r.get("metric_key"), "value": r.get("value")}
                for r in rows
            ],
        })
        if len(focus) >= limit:
            break
    return focus


def _build_metric_candidate_hints(package: Path, fidelity: dict[str, Any], clean_md: str, registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return _table_metric_candidates(package, fidelity, registry) + _prose_metric_candidates(clean_md)

def _best_table_representation(package: Path, table: dict[str, Any], limit: int) -> tuple[str, str, Optional[str]]:
    folder = package / _portable_relpath(str(table.get("folder") or ""))
    recovered = folder / "table.recovered.json"
    recovery_meta = folder / "recovery.metadata.json"
    records = folder / "table.records.json"
    markdown = folder / "table.md"

    if recovered.exists():
        meta = _read_json(recovery_meta, {}) or {}
        return "structured_visual_recovery", _json_block(_read_json(recovered, {}), limit), meta.get("asset_sha256")
    if records.exists():
        return "docling_structured", _json_block(_read_json(records, []), limit), None
    if markdown.exists():
        return "docling_structured", _trim(_read_text(markdown), limit), None
    return "docling_structured", "[NO STRUCTURED TABLE REPRESENTATION AVAILABLE]", None


def _best_formula_representation(package: Path, formula: dict[str, Any], limit: int) -> tuple[str, str, Optional[str]]:
    folder = package / "formulas" / str(formula.get("formula_id"))
    recovered = folder / "formula.recovered.json"
    recovery_meta = folder / "recovery.metadata.json"
    if recovered.exists():
        obj = _read_json(recovered, {})
        meta = _read_json(recovery_meta, {}) or {}
        return "structured_visual_recovery", _json_block(obj, limit), meta.get("asset_sha256")

    text = str(formula.get("text") or "").strip()
    if text and text != "<!-- formula-not-decoded -->":
        return "docling_structured", _trim(text, limit), None
    return "docling_structured", "[FORMULA UNRESOLVED]", None


def _recovered_figures(package: Path, figures: Iterable[dict[str, Any]], limit: int) -> list[tuple[dict[str, Any], str, Optional[str]]]:
    out = []
    for fig in figures:
        folder = package / "figures" / str(fig.get("figure_id"))
        recovered = folder / "figure.recovered.json"
        meta_path = folder / "recovery.metadata.json"
        if not recovered.exists():
            continue
        obj = _read_json(recovered, {})
        meta = _read_json(meta_path, {}) or {}
        out.append((fig, _json_block(obj, limit), meta.get("asset_sha256")))
    return out


def build_source_bundle(package_dir: str | Path, settings: Settings | None = None) -> SourceBundle:
    settings = settings or get_settings()
    package = Path(package_dir).expanduser().resolve()
    manifest = _read_json(package / "source_package_manifest.json")
    fidelity = _read_json(package / "audit" / "source_fidelity.json")
    if not manifest or not fidelity:
        raise FileNotFoundError(
            f"{package} is not a complete scientific source package; expected source_package_manifest.json and audit/source_fidelity.json"
        )

    source_doc = fidelity.get("document", {})
    source_name = source_doc.get("filename") or "original.pdf"
    source_sha = source_doc.get("sha256") or ""
    summary = fidelity.get("summary", {}) or {}
    initial_fidelity_status = str(summary.get("overall_status") or "unknown")

    clean_rel = (manifest.get("core_exports") or {}).get("clean_markdown", "docling/document.clean.md")
    clean_path = package / _portable_relpath(clean_rel)
    clean_md = _read_text(clean_path)
    if not clean_md:
        raise FileNotFoundError(f"Clean Docling Markdown not found or empty: {clean_path}")

    source_item_registry = _build_source_item_registry(fidelity, clean_md)
    metric_candidate_hints = _build_metric_candidate_hints(package, fidelity, clean_md, source_item_registry)
    metric_candidate_focus = _metric_candidate_focus(metric_candidate_hints)
    qualitative_guardrail_hints = _build_qualitative_guardrail_hints(clean_md)

    queue = fidelity.get("recovery_queue", []) or []
    unresolved = []
    for item in queue:
        status = str(item.get("status") or "")
        if status in {"required", "recommended", "review"}:
            # If a recovered artifact exists, it is no longer unresolved for the bundle.
            typ = item.get("item_type")
            iid = str(item.get("matched_picture_id") or item.get("item_id") or "")
            recovered = None
            if typ == "table":
                recovered = package / "tables" / iid / "table.recovered.json"
            elif typ == "formula":
                recovered = package / "formulas" / iid / "formula.recovered.json"
            elif typ == "figure":
                recovered = package / "figures" / iid / "figure.recovered.json"
            if recovered is None or not recovered.exists():
                unresolved.append(item)

    recoverable_queue = [
        item for item in queue
        if str(item.get("status") or "") in {"required", "recommended", "review"}
    ]
    if unresolved:
        effective_fidelity_status = initial_fidelity_status
    elif recoverable_queue:
        effective_fidelity_status = "pass_after_recovery"
    else:
        effective_fidelity_status = initial_fidelity_status

    parts: list[str] = []
    parts.append("=== FAIRAGRO SCIENTIFIC SOURCE BUNDLE ===")
    parts.append(f"Canonical source: {source_name}")
    parts.append(f"Canonical source SHA256: {source_sha}")
    parts.append(f"Initial source fidelity status: {initial_fidelity_status}")
    parts.append(f"Effective source fidelity status: {effective_fidelity_status}")
    parts.append(
        "Authority policy: the original PDF and authored prose/tables/figures/formulas/captions are scientific sources. "
        "Docling and VLM outputs are representations/transcriptions. Generated generic picture descriptions are not authored evidence."
    )
    parts.append("\n=== SOURCE FIDELITY SUMMARY ===")
    parts.append(_json_block(summary, 20000))

    if unresolved:
        parts.append("\n=== UNRESOLVED SOURCE ITEMS ===")
        parts.append(
            "These are known representation/recovery gaps. Do NOT interpret missing extracted metadata as proof that the PDF lacks the information."
        )
        parts.append(_json_block(unresolved, 30000))

    parts.append("\n=== DETERMINISTIC SOURCE ITEM REGISTRY ===")
    parts.append(
        "Use source_item_id from this registry for structured evidence. page/location are deterministic package metadata; "
        "section_hint is included only when a separate prose reference could be mapped conservatively to a heading."
    )
    parts.append(_json_block(source_item_registry, 30000))

    parts.append("\n=== HIGH-PRIORITY QUANTITATIVE COMPLETENESS CANDIDATES ===")
    parts.append(
        "Compact current-document candidates that deserve an explicit keep/omit decision. They are source-derived discovery hints, "
        "not instructions to invent values. Downstream numerical results and summary-level quality/uncertainty prose are prioritized."
    )
    parts.append(_json_block(metric_candidate_focus, 30000))

    parts.append("\n=== QUANTITATIVE METRIC CANDIDATE HINTS ===")
    parts.append(
        "These are deterministic discovery hints, not fitness judgments. Inspect the underlying authored table/prose before using them. "
        "When you select a target from a structured table, preserve its supported sibling DFFP metrics losslessly."
    )
    parts.append(_json_block(metric_candidate_hints, 45000))

    parts.append("\n=== HIGH-PRIORITY QUALITATIVE GUARDRAIL CANDIDATES ===")
    parts.append(
        "These are verbatim/authored limitation, caveat, or risk-section excerpts surfaced deterministically as an "
        "omission checklist. Preserve each substantively distinct DFFP-relevant condition, uncertainty boundary, "
        "validation caveat, and misuse risk. Do not convert them into new fitness judgments."
    )
    parts.append(_json_block(qualitative_guardrail_hints, 30000))

    parts.append("\n=== AUTHORED PROSE: DOCLING CLEAN MARKDOWN ===")
    parts.append(
        "Representation method: docling_clean_markdown. Derived picture descriptions are excluded. "
        "Ignore isolated layout/line-number artifacts when they are clearly serialization noise."
    )
    parts.append(clean_md)

    table_count = 0
    if settings.include_all_structured_tables:
        for table in fidelity.get("tables", []) or []:
            method, content, asset_sha = _best_table_representation(package, table, settings.max_structured_item_chars)
            table_count += 1
            parts.append(
                f"\n=== AUTHOR_TABLE id={table.get('table_id')} page={table.get('page_no')} "
                f"representation={method} asset_sha256={asset_sha or 'null'} ==="
            )
            if table.get("caption"):
                parts.append(f"Caption: {table.get('caption')}")
            parts.append(content)

    formula_count = 0
    if settings.include_formulas:
        for formula in fidelity.get("formulas", []) or []:
            method, content, asset_sha = _best_formula_representation(
                package, formula, settings.max_structured_item_chars
            )
            formula_count += 1
            parts.append(
                f"\n=== AUTHOR_FORMULA id={formula.get('formula_id')} page={formula.get('page_no')} "
                f"representation={method} asset_sha256={asset_sha or 'null'} ==="
            )
            parts.append(content)

    figure_count = 0
    if settings.include_recovered_figures:
        for fig, content, asset_sha in _recovered_figures(
            package, fidelity.get("figures", []) or [], settings.max_structured_item_chars
        ):
            figure_count += 1
            parts.append(
                f"\n=== AUTHOR_FIGURE id={fig.get('figure_id')} page={fig.get('page_no')} "
                f"representation=structured_visual_recovery asset_sha256={asset_sha or 'null'} ==="
            )
            if fig.get("caption"):
                parts.append(f"Authored caption: {fig.get('caption')}")
            parts.append(
                "The JSON below is a machine transcription/structured representation of an authored figure. "
                "It may support explicit evidence only for content visibly transcribed from the figure; do not treat the recovery model's confidence as scientific quality."
            )
            parts.append(content)

    text = "\n".join(parts).strip() + "\n"
    return SourceBundle(
        package_dir=str(package),
        source_filename=source_name,
        source_sha256=source_sha,
        source_fidelity_status=effective_fidelity_status,
        source_fidelity_initial_status=initial_fidelity_status,
        source_fidelity_summary=summary,
        unresolved_items=unresolved,
        clean_markdown_path=str(clean_path),
        text_for_llm=text,
        bundle_sha256=_sha256_text(text),
        item_counts={"tables": table_count, "formulas": formula_count, "recovered_figures": figure_count},
        source_item_registry=source_item_registry,
        metric_candidate_hints=metric_candidate_hints,
        metric_candidate_focus=metric_candidate_focus,
        qualitative_guardrail_hints=qualitative_guardrail_hints,
    )


def save_source_bundle(bundle: SourceBundle, output_path: str | Path | None = None) -> Path:
    package = Path(bundle.package_dir)
    path = Path(output_path) if output_path else package / "dffp" / "source_bundle.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.text_for_llm, encoding="utf-8")
    manifest_path = path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(bundle.manifest_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
