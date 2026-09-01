from __future__ import annotations

"""
Scientific PDF ingestion for FAIRagro-style downstream extraction.

Design goals
------------
1. Keep the original PDF as the canonical source.
2. Use Docling as the layout/provenance backbone, not Markdown as the sole source.
3. Preserve rich Docling JSON + clean Markdown/HTML/DocTags.
4. Export page, table, and figure images plus structured table representations.
5. Audit source fidelity and produce an explicit multimodal-recovery queue.
6. Work in batch across heterogeneous scientific PDFs.

The script intentionally does NOT perform the multimodal recovery itself. It identifies
where recovery is required/recommended and saves the exact source assets needed for a
later VLM step. This keeps preprocessing reproducible and prevents generated visual
summaries from being mistaken for authored scientific evidence.
"""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

try:
    import fitz  # PyMuPDF
except Exception:  # optional but strongly recommended
    fitz = None

# Docling is optional at module-import time so pure audit/caption utilities can be
# unit-tested in lightweight environments. Full PDF ingestion still requires it.
_DOCLING_IMPORT_ERROR = None
try:
    import docling
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        PictureDescriptionApiOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import DoclingDocument
    from docling_core.types.doc.document import (
        DocItemLabel,
        ImageRefMode,
        PictureItem,
        TableItem,
    )
except Exception as exc:  # pragma: no cover - environment dependent
    _DOCLING_IMPORT_ERROR = exc
    docling = None
    InputFormat = None
    PdfPipelineOptions = None
    PictureDescriptionApiOptions = None
    TableFormerMode = None
    DocumentConverter = None
    PdfFormatOption = None
    DoclingDocument = None
    DocItemLabel = None
    ImageRefMode = None
    PictureItem = None
    TableItem = None


def _require_docling() -> None:
    if _DOCLING_IMPORT_ERROR is not None:
        raise ImportError(
            "Docling is required for PDF ingestion. Install/update it with: pip install -U docling"
        ) from _DOCLING_IMPORT_ERROR


SOURCE_PACKAGE_VERSION = "1.1.1"

# Native-PDF/Markdown caption detection must be stricter than generic number
# parsing.  The old expressions made the delimiter after the number optional,
# which caused ordinary prose such as "Figure 5 shows ...",
# "Table 4 illustrates ...", and parenthetical references such as
# "Table A1)" / "Fig. 5)" to be misclassified as real captions.
#
# Real caption candidates therefore require an explicit caption delimiter after
# the number: a period/colon, or an en/em dash/hyphen followed by whitespace.
_CAPTION_DELIMITER = r"(?:[\.:]\s*|[—–-]\s+)"

TABLE_CAPTION_RE = re.compile(
    rf"^\s*table\s+([A-Z0-9IVXLCDM]+)\s*{_CAPTION_DELIMITER}(.*)$",
    re.IGNORECASE,
)
FIGURE_CAPTION_RE = re.compile(
    rf"^\s*(?:figure|fig\.)\s+([A-Z0-9IVXLCDM]+)\s*{_CAPTION_DELIMITER}(.*)$",
    re.IGNORECASE,
)

# Docling item captions are already semantically typed as captions, so number
# extraction can remain permissive.  Keeping this separate prevents the native
# PDF audit from relaxing its definition of a caption just to parse item IDs.
TABLE_NUMBER_PREFIX_RE = re.compile(
    r"^\s*table\s+([A-Z0-9IVXLCDM]+)\b",
    re.IGNORECASE,
)
FIGURE_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:figure|fig\.)\s+([A-Z0-9IVXLCDM]+)\b",
    re.IGNORECASE,
)

HIGH_VALUE_FIGURE_KEYWORDS = {
    "framework",
    "workflow",
    "architecture",
    "hierarchy",
    "hierarchical",
    "taxonomy",
    "criteria",
    "criterion",
    "dimensions",
    "dimension",
    "diagram",
    "schematic",
    "flowchart",
    "decision",
    "process",
    "network",
    "model structure",
    "assessment flow",
}
MEDIUM_VALUE_FIGURE_KEYWORDS = {
    "chart",
    "plot",
    "map",
    "heatmap",
    "matrix",
    "graph",
    "distribution",
    "boxplot",
    "box plot",
    "scatter",
    "histogram",
    "correlation",
    "confusion matrix",
    "spatial",
}


@dataclass
class IngestionConfig:
    input_dir: str = "doc_folder_1"
    output_root: str = "scientific_source_packages"
    recursive: bool = False
    max_pdfs: Optional[int] = None

    # Rendering / Docling
    images_scale: float = 2.0
    table_mode: str = "accurate"  # accurate | fast
    do_formula_enrichment: bool = True
    formula_fail_soft: bool = True
    generate_page_images: bool = True
    generate_picture_images: bool = True
    docling_artifacts_path: Optional[str] = None

    # High-resolution authoritative recovery assets generated from the PDF itself.
    recovery_render_dpi: int = 300
    recovery_crop_padding_pt: float = 8.0

    # OCR routing: auto | always | never
    ocr_mode: str = "auto"
    native_text_char_threshold_per_page: int = 80
    scanned_page_fraction_threshold: float = 0.30

    # Optional Docling picture summaries. OFF by default because these are DERIVED
    # annotations, never authoritative evidence.
    enable_picture_descriptions: bool = False
    picture_description_model: str = "gpt-5.6-luna"
    picture_description_api_url: str = "https://api.openai.com/v1/chat/completions"
    picture_description_api_key_env: str = "OPENAI_API_KEY"
    picture_description_timeout: int = 90

    copy_original_pdf: bool = True
    overwrite_existing_package: bool = False

    # Audit thresholds
    table_low_nonempty_ratio: float = 0.35
    table_review_nonempty_ratio: float = 0.55
    leading_blank_ratio_threshold: float = 0.70

    # Figure matching. Tiny picture objects (logos/caption strips) must not win simply
    # because Docling attached a numbered caption to them.
    figure_min_width_px: int = 180
    figure_min_height_px: int = 80
    figure_min_page_area_ratio: float = 0.008
    figure_preferred_page_area_ratio: float = 0.025
    figure_caption_proximity_pt: float = 220.0

    # Formula consistency checks. Non-empty formula text is still a derived
    # transcription and may need review when regions overlap or look duplicated.
    formula_overlap_iou_threshold: float = 0.45
    formula_text_duplicate_threshold: float = 0.92


# -----------------------------------------------------------------------------
# Generic utilities
# -----------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return str(value)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def safe_stem(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return value or "document"


def normalize_space(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalized_caption(text: str) -> str:
    text = normalize_space(text).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return normalize_space(text)


def caption_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalized_caption(a), normalized_caption(b)).ratio()


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except Exception:
        return "unknown"


def _roman_to_int(value: str) -> Optional[int]:
    value = (value or "").upper().strip()
    if not value or not re.fullmatch(r"[IVXLCDM]+", value):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(value):
        cur = values[ch]
        total += -cur if cur < prev else cur
        prev = max(prev, cur)
    return total if total > 0 else None


def normalize_caption_number(value: Any) -> Optional[str]:
    raw = normalize_space(value).upper()
    if not raw:
        return None
    if raw.isdigit():
        return str(int(raw))
    roman = _roman_to_int(raw)
    return str(roman) if roman is not None else raw


def bbox_to_pdf_top_left(bbox: Optional[dict[str, Any]], page_height: Optional[float]) -> Optional[dict[str, float]]:
    if not bbox:
        return None
    try:
        l, r = float(bbox.get("l")), float(bbox.get("r"))
        t, b = float(bbox.get("t")), float(bbox.get("b"))
    except Exception:
        return None
    origin = str(bbox.get("coord_origin") or "").lower()
    if "bottom" in origin and page_height:
        y0 = float(page_height) - max(t, b)
        y1 = float(page_height) - min(t, b)
    else:
        y0, y1 = min(t, b), max(t, b)
    return {"x0": min(l, r), "y0": y0, "x1": max(l, r), "y1": y1}


def bbox_area(rect: Optional[dict[str, float]]) -> float:
    if not rect:
        return 0.0
    return max(0.0, rect["x1"] - rect["x0"]) * max(0.0, rect["y1"] - rect["y0"])


def bbox_iou(a: Optional[dict[str, float]], b: Optional[dict[str, float]]) -> float:
    if not a or not b:
        return 0.0
    ix0, iy0 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"])
    ix1, iy1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = bbox_area(a) + bbox_area(b) - inter
    return inter / union if union > 0 else 0.0


def horizontal_overlap_ratio(a: Optional[dict[str, float]], b: Optional[dict[str, float]]) -> float:
    if not a or not b:
        return 0.0
    overlap = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    denom = max(1e-9, min(a["x1"] - a["x0"], b["x1"] - b["x0"]))
    return overlap / denom


def bbox_edge_distance(a: Optional[dict[str, float]], b: Optional[dict[str, float]]) -> float:
    if not a or not b:
        return float("inf")
    dx = max(a["x0"] - b["x1"], b["x0"] - a["x1"], 0.0)
    dy = max(a["y0"] - b["y1"], b["y0"] - a["y1"], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def label_value(label: Any) -> str:
    value = getattr(label, "value", label)
    return str(value or "").lower()


def provenance_dict(item: Any) -> dict[str, Any]:
    prov_list = getattr(item, "prov", None) or []
    if not prov_list:
        return {"page_no": None, "bbox": None, "charspan": None}
    prov = prov_list[0]
    bbox = getattr(prov, "bbox", None)
    bbox_dict = None
    if bbox is not None:
        bbox_dict = {
            "l": getattr(bbox, "l", None),
            "t": getattr(bbox, "t", None),
            "r": getattr(bbox, "r", None),
            "b": getattr(bbox, "b", None),
            "coord_origin": str(getattr(getattr(bbox, "coord_origin", None), "value", getattr(bbox, "coord_origin", None))),
        }
    return {
        "page_no": getattr(prov, "page_no", None),
        "bbox": bbox_dict,
        "charspan": getattr(prov, "charspan", None),
    }


def item_annotations(item: Any) -> list[dict[str, Any]]:
    try:
        annotations = item.get_annotations()
    except Exception:
        annotations = getattr(item, "annotations", None) or []
    out: list[dict[str, Any]] = []
    for ann in annotations or []:
        if hasattr(ann, "model_dump"):
            out.append(ann.model_dump(mode="json"))
        else:
            out.append({"repr": repr(ann)})
    return out


def parse_number_from_caption(caption: str, kind: str) -> Optional[str]:
    # Docling has already classified this text as an item caption.  Use the
    # permissive prefix parser here, while native-PDF caption discovery uses the
    # stricter *_CAPTION_RE expressions above.
    regex = TABLE_NUMBER_PREFIX_RE if kind == "table" else FIGURE_NUMBER_PREFIX_RE
    m = regex.match(normalize_space(caption))
    return normalize_caption_number(m.group(1)) if m else None


# -----------------------------------------------------------------------------
# Native PDF inspection (independent reference channel)
# -----------------------------------------------------------------------------


def extract_caption_candidates_from_lines(
    lines: Iterable[str], kind: str
) -> list[dict[str, Any]]:
    regex = TABLE_CAPTION_RE if kind == "table" else FIGURE_CAPTION_RE
    out = []
    for idx, line in enumerate(lines):
        line = normalize_space(line)
        m = regex.match(line)
        if not m:
            continue
        out.append(
            {
                "number_raw": m.group(1).upper(),
                "number": normalize_caption_number(m.group(1)),
                "caption": line,
                "line_index": idx,
            }
        )
    return out


def inspect_pdf_native_text(pdf_path: Path, cfg: IngestionConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": fitz is not None,
        "page_count": None,
        "pages": [],
        "table_captions": [],
        "figure_captions": [],
        "likely_scanned": None,
        "native_text_total_chars": None,
        "median_native_text_chars_per_page": None,
        "low_text_page_fraction": None,
    }
    if fitz is None:
        result["note"] = "PyMuPDF not installed; native PDF cross-check skipped."
        return result

    pdf = fitz.open(pdf_path)
    total_chars = 0
    low_pages = 0
    chars_per_page: list[int] = []
    all_table_caps = []
    all_figure_caps = []
    page_texts = []

    for idx, page in enumerate(pdf):
        page_no = idx + 1
        text = page.get_text("text") or ""
        page_texts.append(text)
        chars = len(re.sub(r"\s+", "", text))
        total_chars += chars
        chars_per_page.append(chars)
        low = chars < cfg.native_text_char_threshold_per_page
        low_pages += int(low)

        lines = text.splitlines()
        table_caps = extract_caption_candidates_from_lines(lines, "table")
        figure_caps = extract_caption_candidates_from_lines(lines, "figure")

        # Native PDF caption geometry provides an independent page/spatial channel for
        # matching Docling items. This is especially important when Docling associates
        # a caption with a tiny logo/caption-strip PictureItem instead of the real figure.
        blocks = page.get_text("blocks", sort=True) or []
        block_candidates: list[tuple[str, dict[str, float]]] = []
        for block in blocks:
            if len(block) < 5:
                continue
            x0, y0, x1, y1, block_text = block[:5]
            for raw_line in str(block_text or "").splitlines():
                norm_line = normalize_space(raw_line)
                if norm_line:
                    block_candidates.append(
                        (norm_line, {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)})
                    )

        def attach_bbox(caps: list[dict[str, Any]], kind: str) -> None:
            regex = TABLE_CAPTION_RE if kind == "table" else FIGURE_CAPTION_RE
            for cap in caps:
                cap["page_no"] = page_no
                cap["bbox"] = None
                best = None
                for block_line, rect in block_candidates:
                    m = regex.match(block_line)
                    if not m:
                        continue
                    number = normalize_caption_number(m.group(1))
                    if number != cap.get("number"):
                        continue
                    score = caption_similarity(cap.get("caption", ""), block_line)
                    if best is None or score > best[0]:
                        best = (score, rect)
                if best is not None:
                    cap["bbox"] = best[1]

        attach_bbox(table_caps, "table")
        attach_bbox(figure_caps, "figure")
        all_table_caps.extend(table_caps)
        all_figure_caps.extend(figure_caps)

        rect = page.rect
        result["pages"].append(
            {
                "page_no": page_no,
                "width": float(rect.width),
                "height": float(rect.height),
                "native_text_chars": chars,
                "low_text": low,
                "embedded_image_count": len(page.get_images(full=True)),
            }
        )

    fraction = low_pages / max(1, len(pdf))
    median_chars = statistics.median(chars_per_page) if chars_per_page else 0
    result.update(
        {
            "page_count": len(pdf),
            "table_captions": all_table_caps,
            "figure_captions": all_figure_caps,
            "likely_scanned": fraction >= cfg.scanned_page_fraction_threshold,
            "native_text_total_chars": total_chars,
            "median_native_text_chars_per_page": median_chars,
            "low_text_page_fraction": fraction,
            "page_text": page_texts,
        }
    )
    pdf.close()
    return result


def decide_ocr(native_audit: dict[str, Any], cfg: IngestionConfig) -> bool:
    mode = cfg.ocr_mode.lower().strip()
    if mode == "always":
        return True
    if mode == "never":
        return False
    if mode != "auto":
        raise ValueError("ocr_mode must be one of: auto, always, never")
    if native_audit.get("available") and native_audit.get("likely_scanned") is not None:
        return bool(native_audit["likely_scanned"])
    # Safe generic fallback when no independent PDF text inspection is available.
    return True


# -----------------------------------------------------------------------------
# Docling setup and conversion
# -----------------------------------------------------------------------------


def build_pdf_pipeline_options(cfg: IngestionConfig, do_ocr: bool) -> PdfPipelineOptions:
    opts = PdfPipelineOptions()

    if hasattr(opts, "do_table_structure"):
        opts.do_table_structure = True
    if hasattr(opts, "table_structure_options"):
        try:
            opts.table_structure_options.mode = (
                TableFormerMode.ACCURATE
                if cfg.table_mode.lower() == "accurate"
                else TableFormerMode.FAST
            )
        except Exception:
            pass
        try:
            opts.table_structure_options.do_cell_matching = True
        except Exception:
            pass

    if hasattr(opts, "do_ocr"):
        opts.do_ocr = bool(do_ocr)

    if hasattr(opts, "do_formula_enrichment"):
        opts.do_formula_enrichment = bool(cfg.do_formula_enrichment)

    artifacts_path = cfg.docling_artifacts_path or os.environ.get("DOCLING_ARTIFACTS_PATH")
    if artifacts_path and hasattr(opts, "artifacts_path"):
        opts.artifacts_path = str(Path(artifacts_path).expanduser().resolve())

    if hasattr(opts, "images_scale"):
        opts.images_scale = float(cfg.images_scale)
    if hasattr(opts, "generate_page_images"):
        opts.generate_page_images = bool(cfg.generate_page_images)
    if hasattr(opts, "generate_picture_images"):
        opts.generate_picture_images = bool(cfg.generate_picture_images)

    # Picture descriptions are intentionally optional. They are DERIVED retrieval
    # metadata, never authored evidence.
    if cfg.enable_picture_descriptions:
        api_key = os.environ.get(cfg.picture_description_api_key_env)
        if not api_key:
            raise RuntimeError(
                "enable_picture_descriptions=True but the configured API-key environment "
                f"variable {cfg.picture_description_api_key_env!r} is not set."
            )
        prompt = (
            "Provide a concise navigation description of this scientific figure. "
            "Do not infer facts that are not visibly present. Mention the figure type, "
            "main subject, and major visible sections. This description is for retrieval "
            "and indexing only, not for scientific data extraction. Do not attempt an "
            "exhaustive transcription."
        )
        picture_opts = PictureDescriptionApiOptions(
            url=cfg.picture_description_api_url,
            prompt=prompt,
            params={
                "model": cfg.picture_description_model,
                "temperature": 0,
                "max_tokens": 300,
            },
            headers={"Authorization": "Bearer " + api_key},
            timeout=cfg.picture_description_timeout,
            provenance="derived_visual_summary; retrieval_only; not_scientific_evidence",
        )
        opts.do_picture_description = True
        opts.picture_description_options = picture_opts
        opts.enable_remote_services = True
    else:
        if hasattr(opts, "do_picture_description"):
            opts.do_picture_description = False
        if hasattr(opts, "enable_remote_services"):
            opts.enable_remote_services = False

    return opts


class ConverterPool:
    """Reuse Docling converters across many PDFs to avoid repeated model startup."""

    def __init__(self, cfg: IngestionConfig):
        _require_docling()
        self.cfg = cfg
        self._pool: dict[tuple[bool, bool], DocumentConverter] = {}

    def get(self, do_ocr: bool, do_formula_enrichment: Optional[bool] = None) -> DocumentConverter:
        formula = self.cfg.do_formula_enrichment if do_formula_enrichment is None else bool(do_formula_enrichment)
        key = (bool(do_ocr), formula)
        if key not in self._pool:
            from dataclasses import replace
            local_cfg = replace(self.cfg, do_formula_enrichment=formula)
            opts = build_pdf_pipeline_options(local_cfg, do_ocr=key[0])
            self._pool[key] = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=opts)
                }
            )
        return self._pool[key]


# -----------------------------------------------------------------------------
# Export helpers
# -----------------------------------------------------------------------------


def save_docling_core_exports(doc: Any, base: Path, include_annotations: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    docling_dir = base / "docling"
    artifacts = (docling_dir / "artifacts").resolve()
    docling_dir.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)

    # Lossless-ish machine representation.
    json_path = docling_dir / "document.docling.json"
    try:
        doc.save_as_json(
            json_path,
            artifacts_dir=artifacts,
            image_mode=ImageRefMode.REFERENCED,
            indent=2,
        )
    except Exception:
        write_json(json_path, doc.export_to_dict())
    out["docling_json"] = str(json_path.relative_to(base))

    # Clean prose/document view: no derived annotations mixed into authored text.
    md_path = docling_dir / "document.clean.md"
    try:
        doc.save_as_markdown(
            md_path,
            artifacts_dir=artifacts,
            image_mode=ImageRefMode.REFERENCED,
            include_annotations=False,
        )
    except TypeError:
        doc.save_as_markdown(md_path, image_mode=ImageRefMode.REFERENCED)
    out["clean_markdown"] = str(md_path.relative_to(base))

    html_path = docling_dir / "document.html"
    try:
        doc.save_as_html(
            html_path,
            artifacts_dir=artifacts,
            image_mode=ImageRefMode.REFERENCED,
            include_annotations=False,
        )
    except TypeError:
        doc.save_as_html(html_path, image_mode=ImageRefMode.REFERENCED)
    out["html"] = str(html_path.relative_to(base))

    txt_path = docling_dir / "document.txt"
    write_text(txt_path, doc.export_to_text())
    out["text"] = str(txt_path.relative_to(base))

    doctags_path = docling_dir / "document.doctags.txt"
    try:
        doctags = doc.export_to_doctags(
            add_location=True,
            add_page_index=True,
            add_table_cell_location=True,
            add_table_cell_text=True,
        )
        write_text(doctags_path, doctags)
        out["doctags"] = str(doctags_path.relative_to(base))
    except Exception as exc:
        write_text(doctags_path, f"DocTags export failed: {exc}")
        out["doctags"] = str(doctags_path.relative_to(base))

    # Separate derived-annotation view, if enabled.
    if include_annotations:
        ann_md = docling_dir / "document.with_derived_annotations.md"
        try:
            doc.save_as_markdown(
                ann_md,
                artifacts_dir=artifacts,
                image_mode=ImageRefMode.REFERENCED,
                include_annotations=True,
                mark_annotations=True,
            )
        except Exception:
            write_text(ann_md, doc.export_to_markdown(include_annotations=True))
        out["derived_annotation_markdown"] = str(ann_md.relative_to(base))

    return out


def save_page_images(doc: Any, base: Path) -> list[dict[str, Any]]:
    pages_dir = base / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for page_no, page in sorted((getattr(doc, "pages", {}) or {}).items()):
        image_ref = getattr(page, "image", None)
        if image_ref is None:
            records.append({"page_no": page_no, "image_path": None})
            continue
        try:
            img = image_ref.pil_image
            path = pages_dir / f"page_{int(page_no):04d}.png"
            img.save(path, "PNG")
            records.append(
                {
                    "page_no": int(page_no),
                    "image_path": str(path.relative_to(base)),
                    "width": img.width,
                    "height": img.height,
                }
            )
        except Exception as exc:
            records.append(
                {"page_no": page_no, "image_path": None, "error": str(exc)}
            )
    return records


def _save_item_image(item: Any, doc: Any, path: Path) -> dict[str, Any]:
    try:
        image = item.get_image(doc)
        if image is None:
            return {"saved": False, "width": None, "height": None}
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG")
        return {
            "saved": True,
            "width": int(getattr(image, "width", 0) or 0),
            "height": int(getattr(image, "height", 0) or 0),
        }
    except Exception as exc:
        return {"saved": False, "width": None, "height": None, "error": str(exc)}


def _page_geometry(native_audit: dict[str, Any], page_no: Optional[int]) -> tuple[Optional[float], Optional[float]]:
    if page_no is None:
        return None, None
    for page in native_audit.get("pages", []) or []:
        if int(page.get("page_no") or -1) == int(page_no):
            return page.get("width"), page.get("height")
    return None, None


def render_pdf_bbox_crop(
    pdf_path: Path,
    page_no: Optional[int],
    bbox: Optional[dict[str, Any]],
    native_audit: dict[str, Any],
    out_path: Path,
    cfg: IngestionConfig,
) -> Optional[str]:
    """Render an authoritative high-resolution recovery crop directly from the PDF."""
    if fitz is None or page_no is None or not bbox:
        return None
    _, page_height = _page_geometry(native_audit, page_no)
    rect_dict = bbox_to_pdf_top_left(bbox, page_height)
    if not rect_dict:
        return None
    pdf = None
    try:
        pdf = fitz.open(pdf_path)
        if int(page_no) < 1 or int(page_no) > len(pdf):
            return None
        page = pdf[int(page_no) - 1]
        pad = float(cfg.recovery_crop_padding_pt)
        rect = fitz.Rect(
            max(page.rect.x0, rect_dict["x0"] - pad),
            max(page.rect.y0, rect_dict["y0"] - pad),
            min(page.rect.x1, rect_dict["x1"] + pad),
            min(page.rect.y1, rect_dict["y1"] + pad),
        )
        if rect.width <= 2 or rect.height <= 2:
            return None
        zoom = max(1.0, float(cfg.recovery_render_dpi) / 72.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return str(out_path)
    except Exception:
        return None
    finally:
        if pdf is not None:
            pdf.close()


def dataframe_elementwise_map(df: pd.DataFrame, func: Any) -> pd.DataFrame:
    """Apply *func* element-wise across pandas 2.x and 3.x.

    ``DataFrame.applymap`` was deprecated in pandas 2.1 and removed in pandas 3.0,
    while ``DataFrame.map`` is not available in older pandas 2.0 releases.  Keeping
    this compatibility shim in one place lets the ingestion pipeline work across
    both API generations without pinning users to an old pandas version.
    """
    mapper = getattr(df, "map", None)
    if callable(mapper):
        return mapper(func)
    applymapper = getattr(df, "applymap", None)
    if callable(applymapper):
        return applymapper(func)
    # Extremely defensive fallback for an unexpected pandas-like DataFrame.
    return df.apply(lambda col: col.map(func))


def table_metrics(table: Any, df: Optional[pd.DataFrame]) -> dict[str, Any]:
    cells = list(getattr(getattr(table, "data", None), "table_cells", None) or [])
    num_rows = int(getattr(getattr(table, "data", None), "num_rows", 0) or 0)
    num_cols = int(getattr(getattr(table, "data", None), "num_cols", 0) or 0)
    merged = sum(
        1
        for c in cells
        if int(getattr(c, "row_span", 1) or 1) > 1
        or int(getattr(c, "col_span", 1) or 1) > 1
    )
    column_headers = sum(bool(getattr(c, "column_header", False)) for c in cells)
    row_headers = sum(bool(getattr(c, "row_header", False)) for c in cells)

    nonempty_ratio = None
    leading_blank_ratio = None
    if df is not None and df.shape[0] > 0 and df.shape[1] > 0:
        values = dataframe_elementwise_map(
            df.fillna("").astype(str),
            lambda x: normalize_space(x),
        )
        nonempty_mask = dataframe_elementwise_map(values, bool)
        nonempty = nonempty_mask.to_numpy().sum()
        nonempty_ratio = float(nonempty / max(1, values.size))
        lead_cols = min(2, values.shape[1])
        leading = values.iloc[:, :lead_cols]
        leading_nonempty = dataframe_elementwise_map(leading, bool)
        leading_blank_ratio = float(
            (~leading_nonempty).to_numpy().sum() / max(1, leading.size)
        )

    return {
        "num_rows": num_rows,
        "num_cols": num_cols,
        "table_cell_count": len(cells),
        "merged_cell_count": merged,
        "column_header_cell_count": column_headers,
        "row_header_cell_count": row_headers,
        "nonempty_ratio": nonempty_ratio,
        "leading_blank_ratio": leading_blank_ratio,
    }


def assess_table_quality(metrics: dict[str, Any], export_error: Optional[str], cfg: IngestionConfig) -> tuple[str, list[str], int]:
    reasons: list[str] = []
    risk = 0
    rows = metrics.get("num_rows") or 0
    cols = metrics.get("num_cols") or 0
    nonempty = metrics.get("nonempty_ratio")
    leading_blank = metrics.get("leading_blank_ratio")
    merged = metrics.get("merged_cell_count") or 0
    headers = metrics.get("column_header_cell_count") or 0

    if export_error:
        reasons.append("TABLE_STRUCTURED_EXPORT_FAILED")
        risk += 5
    if rows < 2 or cols < 2:
        reasons.append("TABLE_STRUCTURE_TOO_SMALL")
        risk += 4
    if nonempty is not None and nonempty < cfg.table_low_nonempty_ratio:
        reasons.append("VERY_HIGH_EMPTY_CELL_RATIO")
        risk += 3
    elif nonempty is not None and nonempty < cfg.table_review_nonempty_ratio:
        reasons.append("HIGH_EMPTY_CELL_RATIO")
        risk += 1
    if cols >= 4 and rows >= 4 and headers == 0:
        reasons.append("NO_COLUMN_HEADERS_DETECTED")
        risk += 1
    if (
        leading_blank is not None
        and leading_blank >= cfg.leading_blank_ratio_threshold
        and rows >= 6
        and merged == 0
    ):
        reasons.append("LEADING_COLUMNS_MOSTLY_BLANK_WITHOUT_MERGED_SPANS")
        risk += 2

    if risk >= 4:
        return "required", reasons, risk
    if risk >= 2:
        return "recommended", reasons, risk
    if risk == 1:
        return "review", reasons, risk
    return "not_needed", reasons, risk


def export_tables(doc: Any, base: Path, cfg: IngestionConfig) -> list[dict[str, Any]]:
    tables_dir = base / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for idx, table in enumerate(getattr(doc, "tables", []) or [], start=1):
        tid = f"tbl_{idx:03d}"
        folder = tables_dir / tid
        folder.mkdir(parents=True, exist_ok=True)
        caption = ""
        try:
            caption = normalize_space(table.caption_text(doc))
        except Exception:
            pass

        export_error = None
        df: Optional[pd.DataFrame] = None
        try:
            df = table.export_to_dataframe(doc=doc)
            df.to_csv(folder / "table.csv", index=False)
            try:
                df.to_json(folder / "table.records.json", orient="records", force_ascii=False, indent=2)
            except TypeError:
                # Older pandas without indent support for this orient.
                write_json(folder / "table.records.json", df.to_dict(orient="records"))
        except Exception as exc:
            export_error = str(exc)

        try:
            write_text(folder / "table.html", table.export_to_html(doc=doc))
        except Exception as exc:
            export_error = export_error or f"HTML export: {exc}"
        try:
            write_text(folder / "table.md", table.export_to_markdown(doc=doc))
        except Exception:
            pass
        try:
            write_text(folder / "table.otsl.txt", table.export_to_otsl(doc=doc))
        except Exception:
            pass
        try:
            write_text(folder / "table.doctags.txt", table.export_to_doctags(doc=doc))
        except Exception:
            pass

        image_path = folder / "table.png"
        image_info = _save_item_image(table, doc, image_path)
        image_saved = bool(image_info.get("saved"))

        metrics = table_metrics(table, df)
        recovery, reasons, risk = assess_table_quality(metrics, export_error, cfg)
        prov = provenance_dict(table)
        record = {
            "table_id": tid,
            "docling_ref": getattr(table, "self_ref", None),
            "caption": caption,
            "table_number": parse_number_from_caption(caption, "table"),
            "page_no": prov["page_no"],
            "bbox": prov["bbox"],
            "metrics": metrics,
            "export_error": export_error,
            "image_path": str(image_path.relative_to(base)) if image_saved else None,
            "image_width": image_info.get("width"),
            "image_height": image_info.get("height"),
            "folder": str(folder.relative_to(base)),
            "multimodal_recovery": recovery,
            "recovery_reason_codes": reasons,
            "risk_score": risk,
            "source_kind": "author_table",
        }
        write_json(folder / "manifest.json", record)
        records.append(record)
    return records


def figure_priority(caption: str, annotations: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    text = (normalize_space(caption) + " " + json.dumps(annotations, ensure_ascii=False)).lower()
    reasons = []
    if any(k in text for k in HIGH_VALUE_FIGURE_KEYWORDS):
        reasons.append("STRUCTURED_SCIENTIFIC_DIAGRAM_OR_HIERARCHY")
        return "recommended", "high", reasons
    if any(k in text for k in MEDIUM_VALUE_FIGURE_KEYWORDS):
        reasons.append("DATA_DENSE_OR_QUANTITATIVE_FIGURE")
        return "recommended", "medium", reasons
    return "optional", "low", reasons


def export_figures(doc: Any, base: Path) -> list[dict[str, Any]]:
    fig_dir = base / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for idx, picture in enumerate(getattr(doc, "pictures", []) or [], start=1):
        fid = f"fig_{idx:03d}"
        folder = fig_dir / fid
        folder.mkdir(parents=True, exist_ok=True)
        try:
            caption = normalize_space(picture.caption_text(doc))
        except Exception:
            caption = ""
        annotations = item_annotations(picture)
        prov = provenance_dict(picture)
        image_path = folder / "figure.png"
        image_info = _save_item_image(picture, doc, image_path)
        image_saved = bool(image_info.get("saved"))
        recovery, priority, reasons = figure_priority(caption, annotations)
        if not image_saved:
            recovery = "required"
            priority = "high"
            reasons.append("PICTURE_IMAGE_NOT_AVAILABLE_FROM_DOCLING")

        record = {
            "figure_id": fid,
            "docling_ref": getattr(picture, "self_ref", None),
            "caption": caption,
            "figure_number": parse_number_from_caption(caption, "figure"),
            "page_no": prov["page_no"],
            "bbox": prov["bbox"],
            "image_path": str(image_path.relative_to(base)) if image_saved else None,
            "image_width": image_info.get("width"),
            "image_height": image_info.get("height"),
            "annotations": annotations,
            "multimodal_recovery": recovery,
            "priority": priority,
            "recovery_reason_codes": reasons,
            "source_kind": "author_figure",
            "derived_annotations_authoritative": False,
        }
        write_json(folder / "manifest.json", record)
        records.append(record)
    return records


def find_formula_items(doc: Any) -> list[Any]:
    out = []
    for item in getattr(doc, "texts", []) or []:
        if label_value(getattr(item, "label", None)) == "formula":
            out.append(item)
    return out


def export_formulas(doc: Any, base: Path) -> list[dict[str, Any]]:
    formula_dir = base / "formulas"
    formula_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for idx, item in enumerate(find_formula_items(doc), start=1):
        form_id = f"formula_{idx:03d}"
        folder = formula_dir / form_id
        folder.mkdir(parents=True, exist_ok=True)
        text = normalize_space(getattr(item, "text", None) or getattr(item, "orig", None))
        prov = provenance_dict(item)
        image_path = folder / "formula.png"
        image_info = _save_item_image(item, doc, image_path)
        image_saved = bool(image_info.get("saved"))
        weak = not text or text == "<!-- formula-not-decoded -->" or len(text) < 2
        recovery = "required" if weak else "not_needed"
        reasons = ["FORMULA_TEXT_MISSING_OR_UNDECODED"] if weak else []
        record = {
            "formula_id": form_id,
            "docling_ref": getattr(item, "self_ref", None),
            "text": text,
            "page_no": prov["page_no"],
            "bbox": prov["bbox"],
            "image_path": str(image_path.relative_to(base)) if image_saved else None,
            "image_width": image_info.get("width"),
            "image_height": image_info.get("height"),
            "multimodal_recovery": recovery,
            "recovery_reason_codes": reasons,
            "source_kind": "author_formula",
        }
        write_json(folder / "manifest.json", record)
        records.append(record)
    return records


def audit_formula_consistency(
    formulas: list[dict[str, Any]],
    native_audit: dict[str, Any],
    base: Path,
    cfg: IngestionConfig,
) -> list[dict[str, Any]]:
    """Add set-level overlap/duplicate checks without pretending decoding is verified."""
    for formula in formulas:
        formula["decoded_text_present"] = bool(normalize_space(formula.get("text")))
        formula["visual_verification_status"] = "unverified"

    for i in range(len(formulas)):
        a = formulas[i]
        _, page_h_a = _page_geometry(native_audit, a.get("page_no"))
        a_rect = bbox_to_pdf_top_left(a.get("bbox"), page_h_a)
        for j in range(i + 1, len(formulas)):
            b = formulas[j]
            if a.get("page_no") != b.get("page_no"):
                continue
            _, page_h_b = _page_geometry(native_audit, b.get("page_no"))
            b_rect = bbox_to_pdf_top_left(b.get("bbox"), page_h_b)
            iou = bbox_iou(a_rect, b_rect)
            text_sim = caption_similarity(a.get("text", ""), b.get("text", ""))
            if iou >= cfg.formula_overlap_iou_threshold:
                for formula, other in ((a, b), (b, a)):
                    code = "FORMULA_REGION_OVERLAPS_ANOTHER_FORMULA"
                    if code not in formula["recovery_reason_codes"]:
                        formula["recovery_reason_codes"].append(code)
                    formula.setdefault("related_formula_ids", []).append(other["formula_id"])
                    if formula["multimodal_recovery"] == "not_needed":
                        formula["multimodal_recovery"] = "review"
            if text_sim >= cfg.formula_text_duplicate_threshold and normalize_space(a.get("text")):
                for formula, other in ((a, b), (b, a)):
                    code = "POSSIBLE_DUPLICATE_FORMULA_EXTRACTION"
                    if code not in formula["recovery_reason_codes"]:
                        formula["recovery_reason_codes"].append(code)
                    formula.setdefault("related_formula_ids", []).append(other["formula_id"])
                    if formula["multimodal_recovery"] == "not_needed":
                        formula["multimodal_recovery"] = "review"

    for formula in formulas:
        # De-duplicate relationship lists and persist the enriched manifest.
        if formula.get("related_formula_ids"):
            formula["related_formula_ids"] = sorted(set(formula["related_formula_ids"]))
        folder = base / "formulas" / formula["formula_id"]
        write_json(folder / "manifest.json", formula)
    return formulas


# -----------------------------------------------------------------------------
# Markdown serialization audit
# -----------------------------------------------------------------------------


def markdown_caption_body_audit(markdown: str) -> dict[str, list[dict[str, Any]]]:
    lines = markdown.splitlines()
    tables = []
    figures = []
    for idx, raw in enumerate(lines):
        line = normalize_space(raw)
        tm = TABLE_CAPTION_RE.match(line)
        if tm:
            window = lines[idx + 1 : idx + 14]
            pipe_lines = [x for x in window if x.count("|") >= 2]
            sep_lines = [x for x in window if re.search(r"\|\s*:?-{3,}", x)]
            tables.append(
                {
                    "number_raw": tm.group(1).upper(),
                    "number": normalize_caption_number(tm.group(1)),
                    "caption": line,
                    "line_no": idx + 1,
                    "markdown_table_body_present": bool(len(pipe_lines) >= 2 and sep_lines),
                }
            )
        fm = FIGURE_CAPTION_RE.match(line)
        if fm:
            figures.append(
                {
                    "number_raw": fm.group(1).upper(),
                    "number": normalize_caption_number(fm.group(1)),
                    "caption": line,
                    "line_no": idx + 1,
                }
            )
    return {"tables": tables, "figures": figures}


# -----------------------------------------------------------------------------
# Caption-to-item reconciliation + multimodal recovery queue
# -----------------------------------------------------------------------------


def _table_caption_match(
    caption: dict[str, Any], items: list[dict[str, Any]], used: set[int]
) -> dict[str, Any]:
    """Conflict-safe table matching: explicit number > page > caption similarity."""
    cap_num = normalize_caption_number(caption.get("number"))
    cap_page = caption.get("page_no")

    available = [(idx, item) for idx, item in enumerate(items) if idx not in used]
    exact_number = [
        (idx, item)
        for idx, item in available
        if item.get("table_number") and normalize_caption_number(item.get("table_number")) == cap_num
    ]
    if exact_number:
        ranked = []
        for idx, item in exact_number:
            page_match = int(item.get("page_no") == cap_page)
            sim = caption_similarity(caption.get("caption", ""), item.get("caption", ""))
            ranked.append((page_match, sim, -idx, idx))
        ranked.sort(reverse=True)
        return {"index": ranked[0][-1], "method": "exact_number", "score": 1.0}

    # Never permit an explicitly numbered item with a conflicting number to win.
    compatible = [
        (idx, item)
        for idx, item in available
        if not item.get("table_number") or normalize_caption_number(item.get("table_number")) == cap_num
    ]
    same_page = [(idx, item) for idx, item in compatible if item.get("page_no") == cap_page]
    if len(same_page) == 1:
        return {"index": same_page[0][0], "method": "unique_same_page", "score": 0.90}
    if same_page:
        ranked = sorted(
            [
                (caption_similarity(caption.get("caption", ""), item.get("caption", "")), idx)
                for idx, item in same_page
            ],
            reverse=True,
        )
        if ranked[0][0] >= 0.35:
            return {"index": ranked[0][1], "method": "same_page_caption", "score": ranked[0][0]}

    ranked = sorted(
        [
            (caption_similarity(caption.get("caption", ""), item.get("caption", "")), idx)
            for idx, item in compatible
        ],
        reverse=True,
    )
    if ranked and ranked[0][0] >= 0.82:
        return {"index": ranked[0][1], "method": "caption_similarity", "score": ranked[0][0]}
    return {"index": None, "method": "unmatched", "score": 0.0}


def _figure_visual_score(
    caption: dict[str, Any],
    item: dict[str, Any],
    native_audit: dict[str, Any],
    cfg: IngestionConfig,
) -> tuple[float, list[str]]:
    """Score whether a Docling PictureItem is the actual scientific figure visual."""
    reasons: list[str] = []
    if item.get("page_no") != caption.get("page_no"):
        return -1e9, ["DIFFERENT_PAGE"]

    score = 0.0
    cap_num = normalize_caption_number(caption.get("number"))
    item_num = normalize_caption_number(item.get("figure_number"))
    if item_num and cap_num and item_num != cap_num:
        # Explicit conflicting figure numbers are never compatible.
        return -1e9, ["CONFLICTING_EXPLICIT_FIGURE_NUMBER"]
    if item_num and cap_num and item_num == cap_num:
        score += 2.0
        reasons.append("EXACT_FIGURE_NUMBER")

    width = int(item.get("image_width") or 0)
    height = int(item.get("image_height") or 0)
    if width >= cfg.figure_min_width_px and height >= cfg.figure_min_height_px:
        score += 2.0
        reasons.append("NONTRIVIAL_IMAGE_SIZE")
    else:
        score -= 4.0
        reasons.append("TINY_PICTURE_OBJECT_PENALTY")

    page_width, page_height = _page_geometry(native_audit, caption.get("page_no"))
    item_rect = bbox_to_pdf_top_left(item.get("bbox"), page_height)
    cap_rect = caption.get("bbox")
    if item_rect and page_width and page_height:
        area_ratio = bbox_area(item_rect) / max(1.0, float(page_width) * float(page_height))
        if area_ratio >= cfg.figure_preferred_page_area_ratio:
            score += 3.0
            reasons.append("LARGE_VISUAL_REGION")
        elif area_ratio >= cfg.figure_min_page_area_ratio:
            score += 1.0
            reasons.append("ADEQUATE_VISUAL_REGION")
        else:
            score -= 3.0
            reasons.append("VERY_SMALL_PAGE_AREA")
    else:
        area_ratio = None

    if item_rect and cap_rect:
        distance = bbox_edge_distance(item_rect, cap_rect)
        overlap = horizontal_overlap_ratio(item_rect, cap_rect)
        if distance <= cfg.figure_caption_proximity_pt:
            score += 2.0
            reasons.append("NEAR_CAPTION")
        elif distance > cfg.figure_caption_proximity_pt * 2.5:
            score -= 1.5
            reasons.append("FAR_FROM_CAPTION")
        if overlap >= 0.35:
            score += 1.5
            reasons.append("HORIZONTAL_CAPTION_ALIGNMENT")

        # In most scientific layouts the figure is above its caption. This is a bonus,
        # not a hard rule, because some journals place captions above figures.
        if item_rect["y1"] <= cap_rect["y1"] + 25:
            score += 0.5

    sim = caption_similarity(caption.get("caption", ""), item.get("caption", ""))
    if sim >= 0.8:
        score += 1.0
        reasons.append("CAPTION_TEXT_SIMILAR")

    return score, reasons


def _figure_caption_match(
    caption: dict[str, Any],
    items: list[dict[str, Any]],
    used: set[int],
    native_audit: dict[str, Any],
    cfg: IngestionConfig,
) -> dict[str, Any]:
    candidates = []
    for idx, item in enumerate(items):
        if idx in used:
            continue
        score, reasons = _figure_visual_score(caption, item, native_audit, cfg)
        if score <= -1e8:
            continue
        candidates.append((score, idx, reasons))
    if not candidates:
        return {"index": None, "method": "unmatched", "score": 0.0, "reasons": []}
    candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    score, idx, reasons = candidates[0]
    # Require positive evidence. This prevents a lone tiny logo on the page from being
    # accepted merely because it is the only PictureItem available.
    if score < 1.0:
        return {"index": None, "method": "insufficient_visual_evidence", "score": score, "reasons": reasons}
    return {"index": idx, "method": "page_spatial_visual", "score": round(score, 3), "reasons": reasons}


def reconcile_pdf_captions(
    native_audit: dict[str, Any],
    tables: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    cfg: IngestionConfig,
) -> dict[str, Any]:
    out = {"tables": [], "figures": []}

    used_tables: set[int] = set()
    for cap in native_audit.get("table_captions", []) or []:
        decision = _table_caption_match(cap, tables, used_tables)
        idx = decision.get("index")
        if idx is None:
            out["tables"].append({
                "caption": cap,
                "matched_item_id": None,
                "status": "missing_docling_item",
                "match_method": decision.get("method"),
                "match_score": decision.get("score"),
            })
        else:
            used_tables.add(idx)
            out["tables"].append({
                "caption": cap,
                "matched_item_id": tables[idx].get("table_id"),
                "status": "matched",
                "match_method": decision.get("method"),
                "match_score": decision.get("score"),
            })

    used_figures: set[int] = set()
    for cap in native_audit.get("figure_captions", []) or []:
        decision = _figure_caption_match(cap, figures, used_figures, native_audit, cfg)
        idx = decision.get("index")
        if idx is None:
            out["figures"].append({
                "caption": cap,
                "matched_item_id": None,
                "status": "missing_docling_visual",
                "match_method": decision.get("method"),
                "match_score": decision.get("score"),
                "match_reasons": decision.get("reasons", []),
            })
        else:
            used_figures.add(idx)
            out["figures"].append({
                "caption": cap,
                "matched_item_id": figures[idx].get("figure_id"),
                "status": "matched",
                "match_method": decision.get("method"),
                "match_score": decision.get("score"),
                "match_reasons": decision.get("reasons", []),
            })
    return out


def build_recovery_queue(
    base: Path,
    pdf_path: Path,
    cfg: IngestionConfig,
    native_audit: dict[str, Any],
    tables: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    formulas: list[dict[str, Any]],
    caption_reconciliation: dict[str, Any],
    markdown_audit: dict[str, Any],
    markdown_text: str,
) -> list[dict[str, Any]]:
    """Build a de-duplicated queue of *scientific* assets needing recovery/review."""
    queue: list[dict[str, Any]] = []
    table_by_id = {t["table_id"]: t for t in tables}
    figure_by_id = {f["figure_id"]: f for f in figures}
    table_match_by_id = {
        x.get("matched_item_id"): x
        for x in caption_reconciliation.get("tables", [])
        if x.get("status") == "matched" and x.get("matched_item_id")
    }

    # Existing Docling tables whose internal structure is poor. Prefer an
    # authoritative high-resolution PDF crop over a derived Docling image when possible.
    for t in tables:
        if t["multimodal_recovery"] not in {"required", "recommended", "review"}:
            continue
        recovery_path = base / "tables" / t["table_id"] / "recovery.png"
        rendered = render_pdf_bbox_crop(
            pdf_path, t.get("page_no"), t.get("bbox"), native_audit, recovery_path, cfg
        )
        asset = str(recovery_path.relative_to(base)) if rendered else t.get("image_path")
        t["recovery_asset_path"] = asset
        write_json(base / "tables" / t["table_id"] / "manifest.json", t)
        matched = table_match_by_id.get(t["table_id"], {})
        source_cap = matched.get("caption", {})
        t["source_table_number"] = source_cap.get("number") or t.get("table_number")
        t["source_caption"] = source_cap.get("caption") or t.get("caption")
        t["scientific_table_match"] = {
            "method": matched.get("match_method"),
            "score": matched.get("match_score"),
        } if matched else None
        write_json(base / "tables" / t["table_id"] / "manifest.json", t)
        queue.append(
            {
                "item_type": "table",
                "item_id": t["table_id"],
                "source_number": t.get("source_table_number"),
                "page_no": t.get("page_no"),
                "caption": t.get("source_caption"),
                "priority": "high" if t["multimodal_recovery"] == "required" else "medium",
                "status": t["multimodal_recovery"],
                "reason_codes": t["recovery_reason_codes"],
                "asset_path": asset,
                "recommended_action": "structured_table_visual_recovery",
            }
        )

    # PDF table captions with no usable Docling TableItem at all.
    for entry in caption_reconciliation.get("tables", []):
        if entry["status"] != "missing_docling_item":
            continue
        cap = entry["caption"]
        queue.append(
            {
                "item_type": "table",
                "item_id": f"pdf_table_{cap.get('number')}",
                "source_number": cap.get("number"),
                "page_no": cap.get("page_no"),
                "caption": cap.get("caption"),
                "priority": "high",
                "status": "required",
                "reason_codes": ["PDF_TABLE_CAPTION_WITHOUT_DOCLING_TABLE_ITEM"],
                "asset_path": f"pages/page_{int(cap.get('page_no')):04d}.png" if cap.get("page_no") else None,
                "recommended_action": "locate_and_crop_table_from_page_then_structured_visual_recovery",
            }
        )

    # Only native-PDF numbered scientific figures are routed by default. This avoids
    # sending logos, author portraits, decorative regions, or caption strips to a VLM.
    matched_figure_ids: set[str] = set()
    for entry in caption_reconciliation.get("figures", []):
        cap = entry["caption"]
        recovery, priority, reasons = figure_priority(cap.get("caption", ""), [])
        if entry["status"] == "matched":
            fid = entry.get("matched_item_id")
            f = figure_by_id.get(fid)
            if not f:
                continue
            matched_figure_ids.add(fid)
            recovery_path = base / "figures" / fid / "recovery.png"
            rendered = render_pdf_bbox_crop(
                pdf_path, f.get("page_no"), f.get("bbox"), native_audit, recovery_path, cfg
            )
            asset = str(recovery_path.relative_to(base)) if rendered else f.get("image_path")
            f["source_figure_number"] = cap.get("number")
            f["source_caption"] = cap.get("caption")
            f["scientific_figure_match"] = {
                "method": entry.get("match_method"),
                "score": entry.get("match_score"),
                "reasons": entry.get("match_reasons", []),
            }
            f["recovery_asset_path"] = asset
            write_json(base / "figures" / fid / "manifest.json", f)
            if recovery in {"required", "recommended"}:
                queue.append(
                    {
                        "item_type": "figure",
                        "item_id": f"pdf_figure_{cap.get('number')}",
                        "matched_picture_id": fid,
                        "source_number": cap.get("number"),
                        "page_no": cap.get("page_no"),
                        "caption": cap.get("caption"),
                        "priority": priority,
                        "status": recovery,
                        "reason_codes": reasons + ["NUMBERED_SCIENTIFIC_FIGURE"],
                        "asset_path": asset,
                        "recommended_action": "task_specific_structured_figure_extraction",
                    }
                )
        else:
            queue.append(
                {
                    "item_type": "figure",
                    "item_id": f"pdf_figure_{cap.get('number')}",
                    "matched_picture_id": None,
                    "source_number": cap.get("number"),
                    "page_no": cap.get("page_no"),
                    "caption": cap.get("caption"),
                    "priority": "high" if priority == "high" else priority,
                    "status": "required",
                    "reason_codes": ["PDF_FIGURE_CAPTION_WITHOUT_RELIABLE_VISUAL_MATCH"] + entry.get("match_reasons", []),
                    "asset_path": f"pages/page_{int(cap.get('page_no')):04d}.png" if cap.get("page_no") else None,
                    "recommended_action": "locate_and_crop_figure_from_page_then_structured_visual_recovery",
                }
            )

    # Preserve high-value unnumbered diagrams only when they have their own real caption
    # and a nontrivial image. They are not conflated with numbered scientific figures.
    for f in figures:
        if f["figure_id"] in matched_figure_ids or not normalize_space(f.get("caption")):
            continue
        recovery, priority, reasons = figure_priority(f.get("caption", ""), f.get("annotations", []))
        if priority != "high":
            continue
        if int(f.get("image_width") or 0) < cfg.figure_min_width_px or int(f.get("image_height") or 0) < cfg.figure_min_height_px:
            continue
        queue.append(
            {
                "item_type": "figure",
                "item_id": f["figure_id"],
                "matched_picture_id": f["figure_id"],
                "source_number": None,
                "page_no": f.get("page_no"),
                "caption": f.get("caption"),
                "priority": "medium",
                "status": "recommended",
                "reason_codes": reasons + ["UNNUMBERED_HIGH_VALUE_SCIENTIFIC_DIAGRAM"],
                "asset_path": f.get("image_path"),
                "recommended_action": "task_specific_structured_figure_extraction",
            }
        )

    for formula in formulas:
        if formula["multimodal_recovery"] not in {"required", "review", "recommended"}:
            continue
        queue.append(
            {
                "item_type": "formula",
                "item_id": formula["formula_id"],
                "source_number": None,
                "page_no": formula.get("page_no"),
                "caption": None,
                "priority": "medium",
                "status": formula["multimodal_recovery"],
                "reason_codes": formula["recovery_reason_codes"],
                "asset_path": formula.get("image_path"),
                "recommended_action": "formula_visual_verification_or_recovery",
            }
        )

    placeholder_count = markdown_text.count("<!-- formula-not-decoded -->")
    weak_formula_count = sum(f["multimodal_recovery"] == "required" for f in formulas)
    if placeholder_count > weak_formula_count:
        queue.append(
            {
                "item_type": "formula",
                "item_id": "unmapped_formula_placeholders",
                "source_number": None,
                "page_no": None,
                "caption": None,
                "priority": "medium",
                "status": "required",
                "reason_codes": ["MARKDOWN_HAS_UNMAPPED_FORMULA_NOT_DECODED_PLACEHOLDERS"],
                "asset_path": None,
                "recommended_action": "locate_formula_regions_from_docling_json_or_page_images",
                "count": placeholder_count - weak_formula_count,
            }
        )

    # Markdown-only serialization losses: useful audit note, no VLM call when the
    # richer Docling table is healthy.
    md_by_number = {x["number"]: x for x in markdown_audit.get("tables", [])}
    table_by_number = {t.get("table_number"): t for t in tables if t.get("table_number")}
    for number, md_item in md_by_number.items():
        if md_item["markdown_table_body_present"]:
            continue
        internal = table_by_number.get(number)
        if internal and internal["multimodal_recovery"] == "not_needed":
            queue.append(
                {
                    "item_type": "serialization",
                    "item_id": f"markdown_table_{number}",
                    "source_number": number,
                    "page_no": internal.get("page_no"),
                    "caption": md_item.get("caption"),
                    "priority": "low",
                    "status": "no_multimodal_recovery_needed",
                    "reason_codes": ["MARKDOWN_TABLE_BODY_MISSING_BUT_DOCLING_TABLE_IS_HEALTHY"],
                    "asset_path": internal.get("folder"),
                    "recommended_action": "use_docling_json_html_or_csv_not_markdown",
                }
            )

    # De-duplicate while retaining the strongest status/priority.
    priority_order = {"high": 0, "medium": 1, "low": 2}
    status_strength = {"required": 3, "recommended": 2, "review": 1, "no_multimodal_recovery_needed": 0}
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for item in queue:
        key = (str(item.get("item_type")), str(item.get("item_id")))
        previous = dedup.get(key)
        if previous is None or status_strength.get(item.get("status"), -1) > status_strength.get(previous.get("status"), -1):
            dedup[key] = item
    queue = list(dedup.values())
    queue.sort(key=lambda x: (priority_order.get(x.get("priority"), 9), x.get("item_type", ""), str(x.get("item_id", ""))))
    return queue


# -----------------------------------------------------------------------------
# Human-readable audit report
# -----------------------------------------------------------------------------


def make_fidelity_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# Source Fidelity Report — {report['document']['filename']}",
        "",
        f"- Package version: `{SOURCE_PACKAGE_VERSION}`",
        f"- SHA-256: `{report['document']['sha256']}`",
        f"- Overall status: **{s['overall_status']}**",
        f"- PDF pages: {s.get('pdf_page_count')}",
        f"- Docling pages: {s.get('docling_page_count')}",
        f"- Detected Docling tables: {s['docling_table_count']}",
        f"- PDF table captions: {s.get('pdf_table_caption_count')}",
        f"- Detected Docling picture objects: {s['docling_picture_count']}",
        f"- PDF numbered figure captions: {s.get('pdf_figure_caption_count')}",
        f"- Reliably matched numbered scientific figures: {s.get('matched_numbered_scientific_figure_count')}",
        f"- Unmatched numbered scientific figures: {s.get('unmatched_numbered_scientific_figure_count')}",
        f"- Formula items: {s['formula_count']}",
        f"- Markdown `formula-not-decoded` placeholders: {s['markdown_formula_placeholder_count']}",
        f"- Multimodal recovery queue items: {s['recovery_queue_count']}",
        f"- High-priority recovery items: {s['high_priority_recovery_count']}",
        "",
        "## Important provenance rule",
        "",
        "The original PDF is the canonical scientific source. Docling JSON/HTML/CSV are structured representations of that source. Any generated picture description is a **derived retrieval annotation** and must not be used as an authored scientific quote/evidence.",
        "",
        "## Numbered table mapping",
        "",
        "| Source table | Page | Matched table | Status | Method | Score |",
        "|---|---:|---|---|---|---:|",
    ]
    for m in report.get("caption_reconciliation", {}).get("tables", []):
        cap = m.get("caption", {})
        lines.append(
            f"| Table {cap.get('number') or ''} | {cap.get('page_no') or ''} | "
            f"{m.get('matched_item_id') or ''} | {m.get('status')} | {m.get('match_method') or ''} | "
            f"{m.get('match_score') if m.get('match_score') is not None else ''} |"
        )

    lines += [
        "",
        "## Table audit",
        "",
        "| ID | Page | Number | Rows×Cols | Nonempty | Recovery | Reasons |",
        "|---|---:|---|---:|---:|---|---|",
    ]
    for t in report["tables"]:
        m = t["metrics"]
        nonempty = m.get("nonempty_ratio")
        nonempty_txt = "" if nonempty is None else f"{nonempty:.2f}"
        lines.append(
            f"| {t['table_id']} | {t.get('page_no') or ''} | {t.get('table_number') or ''} | "
            f"{m.get('num_rows', 0)}×{m.get('num_cols', 0)} | {nonempty_txt} | "
            f"{t['multimodal_recovery']} | {', '.join(t['recovery_reason_codes'])} |"
        )

    lines += [
        "",
        "## Numbered scientific figure mapping",
        "",
        "| Source figure | Page | Matched picture | Status | Method | Score |",
        "|---|---:|---|---|---|---:|",
    ]
    for m in report.get("caption_reconciliation", {}).get("figures", []):
        cap = m.get("caption", {})
        lines.append(
            f"| Figure {cap.get('number') or ''} | {cap.get('page_no') or ''} | "
            f"{m.get('matched_item_id') or ''} | {m.get('status')} | {m.get('match_method') or ''} | "
            f"{m.get('match_score') if m.get('match_score') is not None else ''} |"
        )

    lines += [
        "",
        "## Raw Docling picture-object audit",
        "",
        "| ID | Page | Number | Priority | Recovery | Caption | Reasons |",
        "|---|---:|---|---|---|---|---|",
    ]
    for f in report["figures"]:
        caption = f.get("caption", "").replace("|", "\\|")
        lines.append(
            f"| {f['figure_id']} | {f.get('page_no') or ''} | {f.get('figure_number') or ''} | "
            f"{f.get('priority')} | {f.get('multimodal_recovery')} | {caption[:120]} | "
            f"{', '.join(f.get('recovery_reason_codes', []))} |"
        )

    lines += [
        "",
        "## Formula audit",
        "",
        "| ID | Page | Decoded text | Recovery | Reasons |",
        "|---|---:|---|---|---|",
    ]
    for f in report["formulas"]:
        text = f.get("text", "").replace("|", "\\|")
        lines.append(
            f"| {f['formula_id']} | {f.get('page_no') or ''} | {text[:100]} | {f['multimodal_recovery']} | "
            f"{', '.join(f.get('recovery_reason_codes', []))} |"
        )

    lines += [
        "",
        "## Multimodal recovery queue",
        "",
        "| Priority | Type | ID | Page | Status | Action | Reasons |",
        "|---|---|---|---:|---|---|---|",
    ]
    for q in report["recovery_queue"]:
        lines.append(
            f"| {q.get('priority')} | {q.get('item_type')} | {q.get('item_id')} | "
            f"{q.get('page_no') or ''} | {q.get('status')} | {q.get('recommended_action')} | "
            f"{', '.join(q.get('reason_codes', []))} |"
        )
    return "\n".join(lines) + "\n"


def save_recovery_queue_csv(path: Path, queue: list[dict[str, Any]]) -> None:
    rows = []
    for item in queue:
        row = dict(item)
        row["reason_codes"] = ";".join(item.get("reason_codes", []))
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


# -----------------------------------------------------------------------------
# Prompt templates for a later VLM recovery stage (not executed here)
# -----------------------------------------------------------------------------

TABLE_RECOVERY_PROMPT = """You are recovering the exact structure of a scientific table from an image.
Do not summarize. Do not infer missing values.
Return a structured representation that preserves:
- table title/caption if visible,
- header hierarchy,
- merged cells / row spans / column spans when visible,
- every row label and column label,
- exact cell text, numbers, thresholds, units, symbols and ranges,
- footnotes associated with the table.
For unclear cells, return null and mark uncertainty rather than guessing.
The source image itself is authoritative; your generated text is only a derived extraction.
"""

FIGURE_RECOVERY_PROMPT = """You are extracting scientific structure from a figure image.
Do not write a generic prose summary. Do not infer labels that are not visibly present.
Preserve exact visible labels and explicit relationships.
Extract, when applicable:
- panels and panel labels,
- nodes/categories/entities,
- hierarchy / parent-child relations,
- arrows / directed transitions,
- legends and encodings,
- exact numbers, units, thresholds and statistical values,
- DQ/DA or other applicability markers,
- footnotes or optional/mandatory markers.
If the figure is too dense, recommend region/crop decomposition instead of guessing.
The source image is authoritative; this output is a derived extraction.
"""

FORMULA_RECOVERY_PROMPT = """Transcribe the mathematical expression exactly from the image.
Return LaTeX plus a plain-text rendering. Preserve subscripts, superscripts, roots,
products/sums, fractions and variable names. Do not derive or simplify the formula.
If any symbol is unreadable, mark that symbol as uncertain instead of guessing.
"""


def save_prompt_templates(base: Path) -> None:
    prompt_dir = base / "recovery_prompts"
    write_text(prompt_dir / "table_structured_recovery.txt", TABLE_RECOVERY_PROMPT)
    write_text(prompt_dir / "figure_structured_recovery.txt", FIGURE_RECOVERY_PROMPT)
    write_text(prompt_dir / "formula_recovery.txt", FORMULA_RECOVERY_PROMPT)


# -----------------------------------------------------------------------------
# Per-document ingestion
# -----------------------------------------------------------------------------


def ingest_pdf(pdf_path: Path, cfg: IngestionConfig, pool: Optional[ConverterPool] = None) -> dict[str, Any]:
    _require_docling()
    pdf_path = Path(pdf_path)
    sha = sha256_file(pdf_path)
    package_name = f"{safe_stem(pdf_path.stem)}__{sha[:10]}"
    base = (Path(cfg.output_root) / package_name).resolve()

    resume_docling_json: Optional[Path] = None
    if base.exists() and not cfg.overwrite_existing_package:
        report_path = base / "audit" / "source_fidelity.json"
        if report_path.exists():
            return json.loads(report_path.read_text(encoding="utf-8"))

        # A previous run may have completed the expensive Docling conversion and then
        # failed during export/audit (for example because of a pandas compatibility
        # issue).  Reuse the saved DoclingDocument instead of forcing a 10-20 minute
        # reconversion.
        candidate = base / "docling" / "document.docling.json"
        if candidate.exists():
            resume_docling_json = candidate
            print(f"   -> resuming incomplete package from {candidate}")
        else:
            raise FileExistsError(
                f"Package exists but has no completed report or reusable Docling JSON: {base}. "
                "Use overwrite_existing_package=True to recreate it."
            )

    if base.exists() and cfg.overwrite_existing_package:
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    started = time.time()
    native_audit = inspect_pdf_native_text(pdf_path, cfg)
    do_ocr = decide_ocr(native_audit, cfg)

    if cfg.copy_original_pdf:
        shutil.copy2(pdf_path, base / "original.pdf")

    # Independent native PDF text record.
    if native_audit.get("page_text"):
        native_text = []
        for i, text in enumerate(native_audit["page_text"], start=1):
            native_text.append(f"\n===== PDF PAGE {i} =====\n{text}")
        write_text(base / "native_pdf_text.txt", "".join(native_text))

    formula_fallback = {"used": False, "reason": None}
    conversion_status = "unknown"
    conversion_errors: list[str] = []
    if resume_docling_json is not None:
        conversion_status = "resumed_cached_docling_document"
        try:
            doc = DoclingDocument.load_from_json(resume_docling_json)
        except Exception:
            # Compatibility fallback for Docling-core versions exposing only the
            # Pydantic model validation route.
            doc = DoclingDocument.model_validate(
                json.loads(resume_docling_json.read_text(encoding="utf-8"))
            )
    else:
        if pool is None:
            pool = ConverterPool(cfg)

        converter = pool.get(do_ocr=do_ocr, do_formula_enrichment=cfg.do_formula_enrichment)
        try:
            conv_res = converter.convert(pdf_path)
        except Exception as exc:
            text = str(exc).lower()
            looks_like_enrichment_model_problem = any(
                marker in text
                for marker in (
                    "code_formula",
                    "formula",
                    "huggingface",
                    "snapshot folder",
                    "connecttimeout",
                    "hub",
                )
            )
            if cfg.formula_fail_soft and cfg.do_formula_enrichment and looks_like_enrichment_model_problem:
                print("   -> formula enrichment unavailable; retrying Docling conversion without formula enrichment")
                formula_fallback = {"used": True, "reason": repr(exc)}
                converter = pool.get(do_ocr=do_ocr, do_formula_enrichment=False)
                conv_res = converter.convert(pdf_path)
            else:
                raise
        doc = conv_res.document
        conversion_status = str(
            getattr(getattr(conv_res, "status", None), "value", getattr(conv_res, "status", "unknown"))
        )
        conversion_errors = [str(x) for x in (getattr(conv_res, "errors", None) or [])]

    exports = save_docling_core_exports(
        doc,
        base,
        include_annotations=cfg.enable_picture_descriptions,
    )
    clean_md_path = base / exports["clean_markdown"]
    markdown_text = clean_md_path.read_text(encoding="utf-8")

    page_records = save_page_images(doc, base)
    tables = export_tables(doc, base, cfg)
    figures = export_figures(doc, base)
    formulas = export_formulas(doc, base)
    formulas = audit_formula_consistency(formulas, native_audit, base, cfg)
    md_audit = markdown_caption_body_audit(markdown_text)
    caption_reconciliation = reconcile_pdf_captions(native_audit, tables, figures, cfg)
    recovery_queue = build_recovery_queue(
        base,
        pdf_path,
        cfg,
        native_audit,
        tables,
        figures,
        formulas,
        caption_reconciliation,
        md_audit,
        markdown_text,
    )
    save_prompt_templates(base)

    pdf_table_caption_count = len(native_audit.get("table_captions", []) or [])
    pdf_figure_caption_count = len(native_audit.get("figure_captions", []) or [])
    high_count = sum(q.get("priority") == "high" and q.get("status") != "no_multimodal_recovery_needed" for q in recovery_queue)
    required_count = sum(q.get("status") == "required" for q in recovery_queue)
    recommended_count = sum(q.get("status") == "recommended" for q in recovery_queue)
    review_count = sum(q.get("status") == "review" for q in recovery_queue)
    doc_pages = len(getattr(doc, "pages", {}) or {})

    if required_count:
        overall = "recovery_required"
    elif recommended_count or review_count:
        overall = "multimodal_review_recommended"
    else:
        overall = "pass"

    report = {
        "source_package_version": SOURCE_PACKAGE_VERSION,
        "document": {
            "filename": pdf_path.name,
            "original_path": str(pdf_path.resolve()),
            "sha256": sha,
            "package_dir": str(base.resolve()),
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "docling_version": package_version("docling"),
            "docling_core_version": package_version("docling-core"),
            "pymupdf_version": package_version("PyMuPDF") if fitz is not None else None,
            "pymupdf_available": fitz is not None,
        },
        "config": asdict(cfg),
        "conversion": {
            "ocr_enabled": do_ocr,
            "status": conversion_status,
            "errors": conversion_errors,
            "elapsed_seconds": round(time.time() - started, 2),
            "formula_enrichment_requested": cfg.do_formula_enrichment,
            "formula_enrichment_fallback": formula_fallback,
        },
        "native_pdf_audit": {k: v for k, v in native_audit.items() if k != "page_text"},
        "exports": exports,
        "pages": page_records,
        "tables": tables,
        "figures": figures,
        "formulas": formulas,
        "markdown_serialization_audit": md_audit,
        "caption_reconciliation": caption_reconciliation,
        "recovery_queue": recovery_queue,
        "summary": {
            "overall_status": overall,
            "pdf_page_count": native_audit.get("page_count"),
            "docling_page_count": doc_pages,
            "docling_table_count": len(tables),
            "pdf_table_caption_count": pdf_table_caption_count,
            "docling_picture_count": len(figures),
            "pdf_figure_caption_count": pdf_figure_caption_count,
            "matched_numbered_scientific_figure_count": sum(
                1 for x in caption_reconciliation.get("figures", []) if x.get("status") == "matched"
            ),
            "unmatched_numbered_scientific_figure_count": sum(
                1 for x in caption_reconciliation.get("figures", []) if x.get("status") != "matched"
            ),
            "formula_count": len(formulas),
            "markdown_formula_placeholder_count": markdown_text.count("<!-- formula-not-decoded -->"),
            "recovery_queue_count": len(recovery_queue),
            "high_priority_recovery_count": high_count,
            "required_recovery_count": required_count,
            "recommended_recovery_count": recommended_count,
            "derived_picture_annotations_enabled": cfg.enable_picture_descriptions,
            "derived_picture_annotations_authoritative": False,
        },
    }

    audit_dir = base / "audit"
    write_json(audit_dir / "source_fidelity.json", report)
    write_text(audit_dir / "source_fidelity.md", make_fidelity_markdown(report))
    write_json(audit_dir / "recovery_queue.json", recovery_queue)
    save_recovery_queue_csv(audit_dir / "recovery_queue.csv", recovery_queue)

    manifest = {
        "source_package_version": SOURCE_PACKAGE_VERSION,
        "canonical_source": "original.pdf" if cfg.copy_original_pdf else str(pdf_path.resolve()),
        "source_authority_policy": {
            "authoritative_modalities": ["authored_prose", "author_table", "author_figure", "author_formula", "author_caption"],
            "derived_visual_annotations_authoritative": False,
            "note": "Generated visual descriptions are retrieval/indexing aids only."
        },
        "core_exports": exports,
        "audit": {
            "json": "audit/source_fidelity.json",
            "markdown": "audit/source_fidelity.md",
            "recovery_queue_json": "audit/recovery_queue.json",
            "recovery_queue_csv": "audit/recovery_queue.csv",
        },
        "asset_directories": {
            "pages": "pages/",
            "tables": "tables/",
            "figures": "figures/",
            "formulas": "formulas/",
            "recovery_prompts": "recovery_prompts/",
        },
    }
    write_json(base / "source_package_manifest.json", manifest)
    return report


# -----------------------------------------------------------------------------
# Batch ingestion
# -----------------------------------------------------------------------------


def discover_pdfs(cfg: IngestionConfig) -> list[Path]:
    root = Path(cfg.input_dir)
    pattern = "**/*.pdf" if cfg.recursive else "*.pdf"
    files = sorted(p for p in root.glob(pattern) if p.is_file())
    if cfg.max_pdfs is not None:
        files = files[: cfg.max_pdfs]
    return files


def batch_summary_row(report: dict[str, Any]) -> dict[str, Any]:
    s = report.get("summary", {})
    return {
        "filename": report.get("document", {}).get("filename"),
        "sha256": report.get("document", {}).get("sha256"),
        "overall_status": s.get("overall_status"),
        "pdf_pages": s.get("pdf_page_count"),
        "docling_pages": s.get("docling_page_count"),
        "tables": s.get("docling_table_count"),
        "pdf_table_captions": s.get("pdf_table_caption_count"),
        "figures": s.get("docling_picture_count"),
        "pdf_figure_captions": s.get("pdf_figure_caption_count"),
        "matched_numbered_figures": s.get("matched_numbered_scientific_figure_count"),
        "unmatched_numbered_figures": s.get("unmatched_numbered_scientific_figure_count"),
        "formulas": s.get("formula_count"),
        "formula_placeholders": s.get("markdown_formula_placeholder_count"),
        "recovery_items": s.get("recovery_queue_count"),
        "high_priority_recovery": s.get("high_priority_recovery_count"),
        "required_recovery": s.get("required_recovery_count"),
        "recommended_recovery": s.get("recommended_recovery_count"),
        "package_dir": report.get("document", {}).get("package_dir"),
    }


def ingest_directory(cfg: IngestionConfig) -> dict[str, Any]:
    pdfs = discover_pdfs(cfg)
    if not pdfs:
        raise FileNotFoundError(f"No PDFs found under {cfg.input_dir!r}")

    Path(cfg.output_root).mkdir(parents=True, exist_ok=True)
    pool = ConverterPool(cfg)
    reports = []
    failures = []

    for i, pdf in enumerate(pdfs, start=1):
        print(f"[{i}/{len(pdfs)}] Ingesting {pdf}")
        try:
            report = ingest_pdf(pdf, cfg, pool=pool)
            reports.append(report)
            print(
                "   ->",
                report["summary"]["overall_status"],
                f"tables={report['summary']['docling_table_count']}",
                f"recovery={report['summary']['recovery_queue_count']}",
            )
        except Exception as exc:
            failure = {"filename": str(pdf), "error": repr(exc)}
            failures.append(failure)
            print(f"   !! FAILED: {exc}")

    summary_rows = [batch_summary_row(r) for r in reports]
    summary_df = pd.DataFrame(summary_rows)
    summary_path = Path(cfg.output_root) / "batch_summary.csv"
    summary_df.to_csv(summary_path, index=False)

    all_queue = []
    for report in reports:
        for q in report.get("recovery_queue", []):
            q2 = dict(q)
            q2["document"] = report["document"]["filename"]
            q2["package_dir"] = report["document"]["package_dir"]
            q2["reason_codes"] = ";".join(q2.get("reason_codes", []))
            all_queue.append(q2)
    pd.DataFrame(all_queue).to_csv(
        Path(cfg.output_root) / "recovery_queue_all.csv", index=False
    )
    write_json(Path(cfg.output_root) / "batch_failures.json", failures)

    batch = {
        "reports": reports,
        "failures": failures,
        "summary_csv": str(summary_path.resolve()),
        "recovery_queue_csv": str((Path(cfg.output_root) / "recovery_queue_all.csv").resolve()),
    }
    return batch


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scientific PDF ingestion + source fidelity audit")
    p.add_argument("--input-dir", default="doc_folder_1")
    p.add_argument("--output-dir", default="scientific_source_packages")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--max-pdfs", type=int, default=None)
    p.add_argument("--images-scale", type=float, default=2.0)
    p.add_argument("--ocr", choices=["auto", "always", "never"], default="auto")
    p.add_argument("--table-mode", choices=["accurate", "fast"], default="accurate")
    p.add_argument("--no-formula-enrichment", action="store_true")
    p.add_argument("--no-formula-fail-soft", action="store_true", help="Do not retry without formula enrichment if its model is unavailable")
    p.add_argument("--artifacts-path", default=None, help="Optional local Docling model/artifacts directory")
    p.add_argument("--recovery-dpi", type=int, default=300, help="DPI for authoritative PDF recovery crops")
    p.add_argument("--picture-descriptions", action="store_true")
    p.add_argument("--picture-model", default="gpt-4.1-mini")
    p.add_argument("--picture-api-url", default="https://api.openai.com/v1/chat/completions")
    p.add_argument("--picture-api-key-env", default="OPENAI_API_KEY")
    p.add_argument("--no-copy-pdf", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    cfg = IngestionConfig(
        input_dir=a.input_dir,
        output_root=a.output_dir,
        recursive=a.recursive,
        max_pdfs=a.max_pdfs,
        images_scale=a.images_scale,
        table_mode=a.table_mode,
        do_formula_enrichment=not a.no_formula_enrichment,
        formula_fail_soft=not a.no_formula_fail_soft,
        docling_artifacts_path=a.artifacts_path,
        recovery_render_dpi=a.recovery_dpi,
        ocr_mode=a.ocr,
        enable_picture_descriptions=a.picture_descriptions,
        picture_description_model=a.picture_model,
        picture_description_api_url=a.picture_api_url,
        picture_description_api_key_env=a.picture_api_key_env,
        copy_original_pdf=not a.no_copy_pdf,
        overwrite_existing_package=a.overwrite,
    )
    batch = ingest_directory(cfg)
    print("\nBatch complete")
    print("Summary:", batch["summary_csv"])
    print("Recovery queue:", batch["recovery_queue_csv"])
    if batch["failures"]:
        print("Failures:", len(batch["failures"]))


if __name__ == "__main__":
    main()
