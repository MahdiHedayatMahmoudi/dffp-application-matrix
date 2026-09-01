from __future__ import annotations

"""
FAIRagro Structured Multimodal Recovery Executor
================================================

Consumes a scientific source package produced by scientific_pdf_ingestion.py
(v1.1+ compatible) and executes only the queued visual-recovery tasks.

Principles
----------
- The PDF/table/figure/formula image remains the authoritative source.
- The VLM output is a structured transcription/interpretation layer with provenance.
- Tables are recovered to canonical JSON first, then rendered deterministically to
  Markdown/CSV/HTML.
- Figures and formulas use task-specific schemas instead of generic prose summaries.
- Any OpenAI-compatible vision endpoint can be used (OpenWebUI, OpenAI, LM Studio,
  Ollama-compatible gateways, vLLM, etc.).
- Results are cached by asset SHA256 + task/prompt version.
- No API key is ever written to disk.
"""

import argparse
import base64
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Optional
from urllib.parse import urlparse

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

RECOVERY_VERSION = "0.2"
PROMPT_VERSION = "2026-08-24-v2"


@dataclass
class EndpointConfig:
    api_url: str
    model: str
    api_type: str = "auto"  # auto | responses | chat_completions
    api_key_env: str = "OPENAI_API_KEY"
    timeout: int = 180
    max_tokens: int = 12000
    temperature: Optional[float] = None
    json_mode: str = "auto"  # auto | on | off
    token_param: str = "auto"  # auto | max_tokens | max_completion_tokens
    reasoning_effort: Optional[str] = None
    image_detail: str = "high"


@dataclass
class RecoveryConfig:
    package_dir: str
    endpoint: EndpointConfig
    fallback_endpoint: Optional[EndpointConfig] = None
    statuses: tuple[str, ...] = ("required", "recommended", "review")
    priorities: tuple[str, ...] = ("high", "medium")
    item_types: tuple[str, ...] = ("table", "figure", "formula")
    ids: tuple[str, ...] = ()
    force: bool = False
    dry_run: bool = False
    max_items: Optional[int] = None
    write_enriched_markdown: bool = True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def package_path(package: Path, rel: str) -> Path:
    """Resolve recovery_queue paths written on Windows or POSIX."""
    raw = str(rel or "")
    if "\\" in raw:
        parts = PureWindowsPath(raw).parts
        return package.joinpath(*parts)
    return package / raw


def mime_for(path: Path) -> str:
    s = path.suffix.lower()
    if s in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if s == ".webp":
        return "image/webp"
    return "image/png"


def data_uri(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_for(path)};base64,{b64}"


def endpoint_public_metadata(ep: EndpointConfig) -> dict[str, Any]:
    parsed = urlparse(ep.api_url)
    return {
        "api_url_host": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        "model": ep.model,
        "api_type": resolved_api_type(ep),
        "api_key_env": ep.api_key_env,
        "timeout": ep.timeout,
        "max_tokens": ep.max_tokens,
        "temperature": ep.temperature,
        "json_mode": ep.json_mode,
        "token_param": resolved_token_param(ep),
        "reasoning_effort": ep.reasoning_effort,
        "image_detail": ep.image_detail,
    }


def system_prompt() -> str:
    return (
        "You are a scientific visual transcription engine. The image is an authored "
        "scientific source. Transcribe only information visibly supported by the image. "
        "Do not infer missing labels, repair scientific values from general knowledge, "
        "or silently canonicalize terminology. Preserve visible wording, symbols, units, "
        "inequalities, capitalization, and distinctions. If content is unreadable, use "
        "[UNREADABLE] rather than guessing. Return JSON only, following the requested schema."
    )


def table_prompt(item: dict[str, Any]) -> str:
    caption = item.get("caption") or ""
    return f"""
Recover this scientific table as a flat, semantics-preserving grid.

Source caption:
{caption}

Important rules:
1. Do NOT summarize the table.
2. Preserve every readable data row and every scientifically meaningful column.
3. If the visual uses merged/row-spanned group cells, REPEAT the group label on every
   row it governs. This flattening is intentional so Markdown/CSV preserve hierarchy.
4. If the header is multi-level, combine levels into a concise visible label using
   " / " while preserving the words shown in the image.
5. Preserve exact visible numerical values, ranges, inequality symbols, units,
   abbreviations, score labels, and footnotes. Never correct a value from memory.
6. Section/group banner rows that are not ordinary data rows go in section_rows.
7. Every data row MUST have exactly the same number of cells as columns.
8. Use [UNREADABLE] for an unreadable cell. Use an empty string only when the source
   cell is visibly blank/not applicable.

Return exactly one JSON object with this shape:
{{
  "schema_version": "fairagro_table_recovery_v1",
  "caption": "...",
  "columns": ["...", "..."],
  "rows": [
    ["cell", "cell"],
    ["cell", "cell"]
  ],
  "section_rows": [
    {{"before_row": 0, "text": "visible section/group heading"}}
  ],
  "footnotes": ["exact visible footnote text"],
  "unreadable_notes": ["short description of unresolved regions"],
  "confidence": 0.0
}}

confidence must be between 0 and 1 and reflect transcription confidence, not source quality.
""".strip()


def classify_figure_task(caption: str) -> str:
    c = (caption or "").lower()
    if any(k in c for k in ("workflow", "flow", "process", "decision", "routing")):
        return "workflow"
    if any(k in c for k in ("hierarch", "framework", "criteria", "dimension", "taxonomy", "architecture")):
        return "hierarchy"
    if any(k in c for k in ("map", "plot", "stability", "variability", "accuracy", "consistency", "comparison")):
        return "quantitative"
    return "generic"


def figure_prompt(item: dict[str, Any]) -> tuple[str, str]:
    caption = item.get("caption") or ""
    kind = classify_figure_task(caption)
    base = f"""
Recover structured scientific information from this figure.

Source caption:
{caption}

Do not write a prose summary. Preserve exact visible labels. Do not use surrounding-paper
knowledge to fill labels not visible in this image. Use [UNREADABLE] when necessary.
""".strip()
    if kind == "workflow":
        schema = r'''
Focus on workflow logic, stages, decisions and directed transitions.
Return JSON:
{
  "schema_version": "fairagro_figure_recovery_v1",
  "visual_type": "workflow",
  "caption": "...",
  "nodes": [{"id":"n1","label":"...","role":"stage|decision|input|output|condition|other","group":"... or null"}],
  "edges": [{"source_id":"n1","target_id":"n2","label":"... or null","direction":"directed|undirected"}],
  "visible_labels": ["..."],
  "panels": [{"label":"...","description":"only visibly supported structural content"}],
  "quantitative_items": [{"label":"...","value":"...","unit":"... or null","context":"..."}],
  "legend_items": ["..."],
  "unreadable_notes": ["..."],
  "confidence": 0.0
}
'''
    elif kind == "hierarchy":
        schema = r'''
Focus on hierarchy/containment and exact labels.
Return JSON:
{
  "schema_version": "fairagro_figure_recovery_v1",
  "visual_type": "hierarchy",
  "caption": "...",
  "nodes": [{"id":"n1","label":"...","role":"root|group|domain|dimension|criterion|metric|other","group":"... or null","parent_id":"n0 or null"}],
  "edges": [{"source_id":"n0","target_id":"n1","label":"contains|relates_to|flows_to|...","direction":"directed|undirected"}],
  "visible_labels": ["..."],
  "panels": [{"label":"...","description":"only visibly supported structural content"}],
  "quantitative_items": [{"label":"...","value":"...","unit":"... or null","context":"..."}],
  "legend_items": ["..."],
  "unreadable_notes": ["..."],
  "confidence": 0.0
}
'''
    elif kind == "quantitative":
        schema = r'''
Focus on panels, plotted quantities, visible statistics/values, legends and categorical ratings.
Return JSON:
{
  "schema_version": "fairagro_figure_recovery_v1",
  "visual_type": "quantitative",
  "caption": "...",
  "nodes": [],
  "edges": [],
  "visible_labels": ["..."],
  "panels": [{"label":"(a)","description":"visible content only"}],
  "quantitative_items": [{"label":"...","value":"...","unit":"... or null","context":"panel/series/context"}],
  "legend_items": ["..."],
  "unreadable_notes": ["..."],
  "confidence": 0.0
}
'''
    else:
        schema = r'''
Return JSON:
{
  "schema_version": "fairagro_figure_recovery_v1",
  "visual_type": "generic",
  "caption": "...",
  "nodes": [{"id":"n1","label":"...","role":"other","group":"... or null","parent_id":null}],
  "edges": [],
  "visible_labels": ["..."],
  "panels": [{"label":"...","description":"visible content only"}],
  "quantitative_items": [{"label":"...","value":"...","unit":"... or null","context":"..."}],
  "legend_items": ["..."],
  "unreadable_notes": ["..."],
  "confidence": 0.0
}
'''
    return kind, base + "\n\n" + schema.strip()


def formula_prompt(item: dict[str, Any], package: Path) -> str:
    existing = ""
    iid = item.get("item_id")
    manifest = package / "formulas" / str(iid) / "manifest.json"
    if manifest.exists():
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8")).get("text") or ""
        except Exception:
            pass
    return f"""
Visually verify/transcribe this authored scientific formula.

Existing Docling transcription (derived, potentially wrong):
{existing or '[none]'}

Rules:
- The image is authoritative.
- Preserve root indices, superscripts, subscripts, product/sum limits, Greek symbols,
  multiplication operators, parentheses and equality structure.
- Do not simplify the formula.
- If the crop is partial or overlaps another formula, say so.

Return JSON:
{{
  "schema_version": "fairagro_formula_recovery_v1",
  "latex": "verified LaTeX or [UNREADABLE]",
  "plain_text": "faithful linear reading",
  "variables": [{{"symbol":"...","meaning":"only if visibly defined, else null"}}],
  "is_complete_crop": true,
  "discrepancies_from_docling": ["..."],
  "unreadable_notes": ["..."],
  "confidence": 0.0
}}
""".strip()


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for x in content:
            if isinstance(x, dict):
                if isinstance(x.get("text"), str):
                    chunks.append(x["text"])
                elif isinstance(x.get("content"), str):
                    chunks.append(x["content"])
            elif isinstance(x, str):
                chunks.append(x)
        return "\n".join(chunks)
    return str(content or "")


def resolved_api_type(ep: EndpointConfig) -> str:
    if ep.api_type in {"responses", "chat_completions"}:
        return ep.api_type
    path = urlparse(ep.api_url).path.rstrip("/").lower()
    if path.endswith("/responses"):
        return "responses"
    return "chat_completions"


def resolved_token_param(ep: EndpointConfig) -> str:
    if ep.token_param in {"max_tokens", "max_completion_tokens"}:
        return ep.token_param
    # Modern OpenAI reasoning/frontier models use max_completion_tokens on Chat Completions.
    if re.match(r"^(gpt-5|o[1-9]|chatgpt-)", ep.model, re.I):
        return "max_completion_tokens"
    return "max_tokens"


def extract_response_text(payload: dict[str, Any], api_type: str) -> str:
    if api_type == "chat_completions":
        try:
            return _message_content_to_text(payload["choices"][0]["message"]["content"])
        except Exception:
            pass
    else:
        # Responses API typically returns message items under output[].content[].
        top = payload.get("output_text")
        if isinstance(top, str) and top.strip():
            return top
        chunks: list[str] = []
        for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content", []) if isinstance(item.get("content"), list) else []:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    chunks.append(text["value"])
        if chunks:
            return "\n".join(chunks)

    for key in ("response", "content", "text", "output_text"):
        if key in payload:
            val = _message_content_to_text(payload[key])
            if val.strip():
                return val
    raise ValueError("Could not locate assistant text in endpoint response")


def strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S | re.I)
    return m.group(1).strip() if m else t


def first_json_object(text: str) -> dict[str, Any]:
    t = strip_code_fence(text)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    start = t.find("{")
    if start < 0:
        raise ValueError("No JSON object found in model response")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("Unbalanced JSON object in model response")


def _http_error(response: requests.Response, payload: dict[str, Any], api_type: str) -> RuntimeError:
    request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
    try:
        body: Any = response.json()
        body_text = json.dumps(body, ensure_ascii=False, indent=2)
    except Exception:
        body_text = response.text[:8000]
    # Do not print the image/base64 payload. Only expose safe request metadata.
    safe_request = {
        "api_type": api_type,
        "model": payload.get("model"),
        "max_output": payload.get("max_output_tokens")
            or payload.get("max_completion_tokens")
            or payload.get("max_tokens"),
        "has_temperature": "temperature" in payload,
        "has_response_format": "response_format" in payload,
        "has_text_format": isinstance(payload.get("text"), dict),
        "has_reasoning": isinstance(payload.get("reasoning"), dict),
    }
    return RuntimeError(
        f"HTTP {response.status_code} from {response.url}\n"
        f"Request ID: {request_id or '[not provided]'}\n"
        f"Request summary: {json.dumps(safe_request, ensure_ascii=False)}\n"
        f"API response:\n{body_text}"
    )


def _post(ep: EndpointConfig, payload: dict[str, Any], api_type: str) -> requests.Response:
    key = os.environ.get(ep.api_key_env, "").strip()
    if not key:
        raise RuntimeError(
            f"API key environment variable {ep.api_key_env!r} is empty. "
            "Put the key in .env or set it in PowerShell before running."
        )
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    response = requests.post(ep.api_url, headers=headers, json=payload, timeout=ep.timeout)
    if not response.ok:
        raise _http_error(response, payload, api_type)
    return response


def _responses_payload(ep: EndpointConfig, prompt: str, image: Path, use_json_mode: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": ep.model,
        "instructions": system_prompt(),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": data_uri(image),
                        "detail": ep.image_detail,
                    },
                ],
            }
        ],
        "max_output_tokens": ep.max_tokens,
    }
    if ep.reasoning_effort:
        payload["reasoning"] = {"effort": ep.reasoning_effort}
    # Keep Responses API JSON mode conservative and portable. The prompt explicitly
    # requires JSON, and the parser validates it. Structured-output schemas can be
    # added later per task without changing scientific semantics.
    return payload


def _chat_payload(ep: EndpointConfig, prompt: str, image: Path, use_json_mode: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": ep.model,
        "messages": [
            {"role": "system", "content": system_prompt()},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri(image), "detail": ep.image_detail},
                    },
                ],
            },
        ],
        resolved_token_param(ep): ep.max_tokens,
    }
    if ep.temperature is not None:
        payload["temperature"] = ep.temperature
    if ep.reasoning_effort:
        payload["reasoning_effort"] = ep.reasoning_effort
    if use_json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def call_endpoint(ep: EndpointConfig, prompt: str, image: Path, allow_json_mode: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    api_type = resolved_api_type(ep)
    use_json_mode = allow_json_mode and ep.json_mode in {"auto", "on"}
    payload = (
        _responses_payload(ep, prompt, image, use_json_mode)
        if api_type == "responses"
        else _chat_payload(ep, prompt, image, use_json_mode)
    )

    started = time.time()
    try:
        response = _post(ep, payload, api_type)
    except RuntimeError as exc:
        # Some OpenAI-compatible Chat Completions servers reject response_format.
        # Only auto mode is retried; explicit --json-mode on remains strict.
        if api_type == "chat_completions" and use_json_mode and ep.json_mode == "auto" and "response_format" in payload:
            payload.pop("response_format", None)
            response = _post(ep, payload, api_type)
        else:
            raise exc

    raw = response.json()
    text = extract_response_text(raw, api_type)
    obj = first_json_object(text)
    usage = raw.get("usage") if isinstance(raw, dict) else None
    meta = {
        "elapsed_seconds": round(time.time() - started, 3),
        "usage": usage,
        "raw_text": text,
        "request_id": response.headers.get("x-request-id") or response.headers.get("request-id"),
        "api_type": api_type,
    }
    return obj, meta

def validate_and_normalize_table(obj: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    cols = obj.get("columns")
    rows = obj.get("rows")
    if not isinstance(cols, list) or not cols:
        raise ValueError("Recovered table has no non-empty columns array")
    cols = [str(x if x is not None else "") for x in cols]
    if not isinstance(rows, list):
        raise ValueError("Recovered table rows is not an array")
    normalized = []
    for i, row in enumerate(rows):
        if not isinstance(row, list):
            warnings.append(f"row_{i}_was_not_array")
            row = [str(row)]
        row = [str(x if x is not None else "") for x in row]
        if len(row) < len(cols):
            warnings.append(f"row_{i}_padded_{len(cols)-len(row)}")
            row = row + [""] * (len(cols) - len(row))
        elif len(row) > len(cols):
            warnings.append(f"row_{i}_had_{len(row)-len(cols)}_extra_cells_joined")
            row = row[: len(cols)-1] + [" | ".join(row[len(cols)-1:])]
        normalized.append(row)
    obj["columns"] = cols
    obj["rows"] = normalized
    obj.setdefault("section_rows", [])
    obj.setdefault("footnotes", [])
    obj.setdefault("unreadable_notes", [])
    return obj, warnings


def md_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", r"\|").replace("\n", "<br>").strip()


def render_table_markdown(obj: dict[str, Any]) -> str:
    cols = obj.get("columns") or []
    rows = obj.get("rows") or []
    sections = obj.get("section_rows") or []
    sec_map: dict[int, list[str]] = {}
    for s in sections:
        try:
            sec_map.setdefault(int(s.get("before_row", 0)), []).append(str(s.get("text") or ""))
        except Exception:
            pass
    out = []
    cap = str(obj.get("caption") or "").strip()
    if cap:
        out.append(f"**{cap}**")
        out.append("")
    out.append("| " + " | ".join(md_escape(x) for x in cols) + " |")
    out.append("| " + " | ".join("---" for _ in cols) + " |")
    for i, row in enumerate(rows):
        for sec in sec_map.get(i, []):
            if sec:
                # A section marker outside the table is more portable than colspan Markdown.
                out.append("")
                out.append(f"**{md_escape(sec)}**")
                out.append("")
                out.append("| " + " | ".join(md_escape(x) for x in cols) + " |")
                out.append("| " + " | ".join("---" for _ in cols) + " |")
        out.append("| " + " | ".join(md_escape(x) for x in row) + " |")
    foot = obj.get("footnotes") or []
    if foot:
        out.append("")
        out.append("**Footnotes**")
        for x in foot:
            out.append(f"- {str(x).strip()}")
    return "\n".join(out).rstrip() + "\n"


def render_table_csv(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(obj.get("columns") or [])
        w.writerows(obj.get("rows") or [])


def render_table_html(obj: dict[str, Any]) -> str:
    cols = obj.get("columns") or []
    rows = obj.get("rows") or []
    lines = ["<table>", "  <thead><tr>" + "".join(f"<th>{html.escape(str(c))}</th>" for c in cols) + "</tr></thead>", "  <tbody>"]
    for row in rows:
        lines.append("    <tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>")
    lines += ["  </tbody>", "</table>"]
    return "\n".join(lines) + "\n"


def render_figure_markdown(obj: dict[str, Any]) -> str:
    out = []
    if obj.get("caption"):
        out.append(f"**{obj['caption']}**")
    out.append(f"\nVisual type: `{obj.get('visual_type','unknown')}`")
    labels = obj.get("visible_labels") or []
    if labels:
        out.append("\nVisible labels: " + "; ".join(str(x) for x in labels))
    nodes = obj.get("nodes") or []
    if nodes:
        out.append("\n| id | label | role | parent/group |")
        out.append("| --- | --- | --- | --- |")
        for n in nodes:
            out.append(f"| {md_escape(n.get('id'))} | {md_escape(n.get('label'))} | {md_escape(n.get('role'))} | {md_escape(n.get('parent_id') or n.get('group'))} |")
    edges = obj.get("edges") or []
    if edges:
        out.append("\n| source | target | relation |")
        out.append("| --- | --- | --- |")
        for e in edges:
            out.append(f"| {md_escape(e.get('source_id'))} | {md_escape(e.get('target_id'))} | {md_escape(e.get('label'))} |")
    return "\n".join(out).rstrip() + "\n"


def select_items(queue: list[dict[str, Any]], cfg: RecoveryConfig) -> list[dict[str, Any]]:
    ids = set(cfg.ids)
    selected = []
    for item in queue:
        iid = str(item.get("item_id"))
        if ids:
            if iid not in ids and str(item.get("matched_picture_id")) not in ids:
                continue
        else:
            if str(item.get("status")) not in cfg.statuses:
                continue
            if str(item.get("priority")) not in cfg.priorities:
                continue
            if str(item.get("item_type")) not in cfg.item_types:
                continue
            # Some audit records (for example unmapped formula placeholders)
            # require asset localization before a VLM can be called. Keep them in
            # source_fidelity/recovery_queue, but do not fail the bulk executor.
            if not item.get("asset_path"):
                continue
        selected.append(item)
    if cfg.max_items is not None:
        selected = selected[: cfg.max_items]
    return selected


def cache_path(package: Path, image_sha: str, task: str) -> Path:
    return package / "recovery" / "cache" / f"{image_sha}__{task}__{PROMPT_VERSION}.json"


def item_output_folder(package: Path, item: dict[str, Any]) -> Path:
    typ = item.get("item_type")
    if typ == "table":
        return package / "tables" / str(item.get("item_id"))
    if typ == "formula":
        return package / "formulas" / str(item.get("item_id"))
    pic = item.get("matched_picture_id") or item.get("item_id")
    return package / "figures" / str(pic)


def recover_one(package: Path, item: dict[str, Any], cfg: RecoveryConfig) -> dict[str, Any]:
    typ = str(item.get("item_type"))
    asset = package_path(package, str(item.get("asset_path") or ""))
    if not asset.exists():
        raise FileNotFoundError(f"Recovery asset not found: {asset}")
    image_sha = sha256_file(asset)
    if typ == "table":
        task = "table"
        prompt = table_prompt(item)
    elif typ == "formula":
        task = "formula"
        prompt = formula_prompt(item, package)
    else:
        task, prompt = figure_prompt(item)
        task = f"figure_{task}"

    cp = cache_path(package, image_sha, task)
    cached = False
    endpoint_used = cfg.endpoint
    call_meta: dict[str, Any] = {}
    if cp.exists() and not cfg.force:
        cached_blob = json.loads(cp.read_text(encoding="utf-8"))
        obj = cached_blob["result"]
        call_meta = cached_blob.get("call_meta") or {}
        cached = True
    else:
        try:
            obj, call_meta = call_endpoint(cfg.endpoint, prompt, asset)
        except Exception as primary_exc:
            if cfg.fallback_endpoint is None:
                raise
            endpoint_used = cfg.fallback_endpoint
            obj, call_meta = call_endpoint(cfg.fallback_endpoint, prompt, asset)
            call_meta["primary_error"] = repr(primary_exc)
        blob = {
            "cache_version": RECOVERY_VERSION,
            "prompt_version": PROMPT_VERSION,
            "asset_sha256": image_sha,
            "task": task,
            "endpoint": endpoint_public_metadata(endpoint_used),
            "created_at": now_iso(),
            "result": obj,
            "call_meta": {k: v for k, v in call_meta.items() if k != "raw_text"},
        }
        write_json(cp, blob)

    renderer_warnings: list[str] = []
    if typ == "table":
        obj, renderer_warnings = validate_and_normalize_table(obj)

    out_folder = item_output_folder(package, item)
    out_folder.mkdir(parents=True, exist_ok=True)
    if typ == "table":
        write_json(out_folder / "table.recovered.json", obj)
        write_text(out_folder / "table.recovered.md", render_table_markdown(obj))
        render_table_csv(out_folder / "table.recovered.csv", obj)
        write_text(out_folder / "table.recovered.html", render_table_html(obj))
        write_text(out_folder / "table.best_available.md", render_table_markdown(obj))
    elif typ == "formula":
        write_json(out_folder / "formula.recovered.json", obj)
        write_text(out_folder / "formula.recovered.md", f"```latex\n{obj.get('latex','')}\n```\n")
    else:
        write_json(out_folder / "figure.recovered.json", obj)
        write_text(out_folder / "figure.recovered.md", render_figure_markdown(obj))

    meta = {
        "recovery_version": RECOVERY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "item_type": typ,
        "item_id": item.get("item_id"),
        "matched_picture_id": item.get("matched_picture_id"),
        "asset_path": str(item.get("asset_path")),
        "asset_sha256": image_sha,
        "caption": item.get("caption"),
        "page_no": item.get("page_no"),
        "cached": cached,
        "endpoint": endpoint_public_metadata(endpoint_used),
        "completed_at": now_iso(),
        "renderer_warnings": renderer_warnings,
        "confidence": obj.get("confidence"),
        "usage": call_meta.get("usage"),
        "elapsed_seconds": call_meta.get("elapsed_seconds"),
    }
    write_json(out_folder / "recovery.metadata.json", meta)
    return meta


def build_enriched_markdown(package: Path) -> Optional[Path]:
    table_aware = package / "docling" / "document.table_aware.md"
    clean = package / "docling" / "document.clean.md"
    src = table_aware if table_aware.exists() else clean
    if not src.exists():
        return None
    text = src.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- FAIRAGRO_TABLE_SLOT_START table_id=(?P<id>[^\s]+).*?-->.*?<!-- FAIRAGRO_TABLE_SLOT_END table_id=(?P=id) -->",
        re.S,
    )

    def repl(m: re.Match[str]) -> str:
        tid = m.group("id")
        recovered = package / "tables" / tid / "table.recovered.md"
        if not recovered.exists():
            return m.group(0)
        body = recovered.read_text(encoding="utf-8").strip()
        return (
            f"<!-- FAIRAGRO_RECOVERED_TABLE_START table_id={tid} source_modality=author_table "
            f"extraction=structured_visual_recovery -->\n{body}\n"
            f"<!-- FAIRAGRO_RECOVERED_TABLE_END table_id={tid} -->"
        )

    enriched = pattern.sub(repl, text)
    out = package / "docling" / "document.enriched.md"
    write_text(out, enriched)
    return out


def run_recovery(cfg: RecoveryConfig) -> dict[str, Any]:
    package = Path(cfg.package_dir).resolve()
    queue_path = package / "audit" / "recovery_queue.json"
    if not queue_path.exists():
        raise FileNotFoundError(f"No recovery queue: {queue_path}")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    selected = select_items(queue, cfg)

    print(f"Package: {package}")
    print(f"Selected recovery items: {len(selected)}")
    for x in selected:
        print(f"  - {x.get('item_id')} [{x.get('item_type')}] {x.get('priority')}/{x.get('status')} -> {x.get('asset_path')}")
    if cfg.dry_run:
        return {"selected": selected, "dry_run": True}

    results = []
    failures = []
    for i, item in enumerate(selected, start=1):
        print(f"[{i}/{len(selected)}] Recovering {item.get('item_id')} ({item.get('item_type')})")
        try:
            meta = recover_one(package, item, cfg)
            results.append(meta)
            print(f"   -> ok confidence={meta.get('confidence')} cached={meta.get('cached')}")
        except Exception as exc:
            failure = {"item_id": item.get("item_id"), "item_type": item.get("item_type"), "error": repr(exc)}
            failures.append(failure)
            print(f"   !! FAILED: {exc}")

    enriched = build_enriched_markdown(package) if cfg.write_enriched_markdown else None
    audit = package / "audit"
    write_json(audit / "recovery_results.json", {"results": results, "failures": failures})
    with (audit / "recovery_results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["item_id", "item_type", "page_no", "cached", "confidence", "elapsed_seconds", "asset_sha256"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k) for k in fields})
    manifest = {
        "recovery_version": RECOVERY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "created_at": now_iso(),
        "package_dir": str(package),
        "primary_endpoint": endpoint_public_metadata(cfg.endpoint),
        "fallback_endpoint": endpoint_public_metadata(cfg.fallback_endpoint) if cfg.fallback_endpoint else None,
        "selected_count": len(selected),
        "success_count": len(results),
        "failure_count": len(failures),
        "enriched_markdown": str(enriched.relative_to(package)) if enriched else None,
    }
    write_json(package / "recovery" / "recovery_manifest.json", manifest)
    return manifest


def csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in (value or "").split(",") if x.strip())


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FAIRagro structured multimodal recovery executor")
    p.add_argument("--package-dir", required=True)
    p.add_argument(
        "--api-url",
        default=os.getenv("FAIRAGRO_VISION_API_URL", "https://api.openai.com/v1/responses"),
        help="Vision API endpoint. Defaults to OpenAI Responses API.",
    )
    p.add_argument(
        "--api-type",
        choices=["auto", "responses", "chat_completions"],
        default=os.getenv("FAIRAGRO_VISION_API_TYPE", "auto"),
        help="API request shape. auto infers from URL.",
    )
    p.add_argument(
        "--model",
        default=os.getenv("FAIRAGRO_VISION_MODEL", "gpt-5.6-luna"),
        help="Vision-capable model name.",
    )
    p.add_argument(
        "--api-key-env",
        default=os.getenv("FAIRAGRO_VISION_API_KEY_ENV", "OPENAI_API_KEY"),
        help="Environment variable containing the API key.",
    )
    p.add_argument("--timeout", type=int, default=int(os.getenv("FAIRAGRO_VISION_TIMEOUT", "180")))
    p.add_argument("--max-tokens", type=int, default=int(os.getenv("FAIRAGRO_VISION_MAX_TOKENS", "12000")))
    p.add_argument("--json-mode", choices=["auto", "on", "off"], default="auto")
    p.add_argument(
        "--token-param",
        choices=["auto", "max_tokens", "max_completion_tokens"],
        default="auto",
        help="Chat Completions output-token parameter. auto is recommended.",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional sampling temperature. Omitted by default for reasoning/frontier model compatibility.",
    )
    p.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh", "max"],
        default=os.getenv("FAIRAGRO_VISION_REASONING_EFFORT") or None,
        help="Optional reasoning effort for models that support it.",
    )
    p.add_argument("--image-detail", choices=["auto", "low", "high"], default="high")

    p.add_argument("--fallback-api-url", default=None)
    p.add_argument("--fallback-api-type", choices=["auto", "responses", "chat_completions"], default="auto")
    p.add_argument("--fallback-model", default=None)
    p.add_argument("--fallback-api-key-env", default="OPENAI_API_KEY")
    p.add_argument("--fallback-json-mode", choices=["auto", "on", "off"], default="auto")

    p.add_argument("--statuses", default="required,recommended,review")
    p.add_argument("--priorities", default="high,medium")
    p.add_argument("--item-types", default="table,figure,formula")
    p.add_argument("--ids", default="", help="Comma-separated queue item IDs; overrides status/priority filters")
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-enriched-markdown", action="store_true")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    primary = EndpointConfig(
        api_url=a.api_url,
        model=a.model,
        api_type=a.api_type,
        api_key_env=a.api_key_env,
        timeout=a.timeout,
        max_tokens=a.max_tokens,
        temperature=a.temperature,
        json_mode=a.json_mode,
        token_param=a.token_param,
        reasoning_effort=a.reasoning_effort,
        image_detail=a.image_detail,
    )
    fallback = None
    if a.fallback_api_url and a.fallback_model:
        fallback = EndpointConfig(
            api_url=a.fallback_api_url,
            model=a.fallback_model,
            api_type=a.fallback_api_type,
            api_key_env=a.fallback_api_key_env,
            timeout=a.timeout,
            max_tokens=a.max_tokens,
            temperature=a.temperature,
            json_mode=a.fallback_json_mode,
            token_param=a.token_param,
            reasoning_effort=a.reasoning_effort,
            image_detail=a.image_detail,
        )
    cfg = RecoveryConfig(
        package_dir=a.package_dir,
        endpoint=primary,
        fallback_endpoint=fallback,
        statuses=csv_tuple(a.statuses),
        priorities=csv_tuple(a.priorities),
        item_types=csv_tuple(a.item_types),
        ids=csv_tuple(a.ids),
        force=a.force,
        dry_run=a.dry_run,
        max_items=a.max_items,
        write_enriched_markdown=not a.no_enriched_markdown,
    )
    manifest = run_recovery(cfg)
    print("\nRecovery complete")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
