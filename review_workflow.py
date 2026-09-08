"""Human review and publication workflow for FAIRagro DFFP records.

This module intentionally does not call an LLM.  It treats a strict-pass
machine extraction as an immutable baseline, creates deterministic review IDs,
records auditable human decisions in a sidecar manifest, and derives a reviewed
matrix without editing the machine artifact in place.

Review provenance is stored outside the extraction schema so reviewed publication
artifacts remain auditable and compatible with their declared machine contract.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from pydantic import ValidationError

from metric_postprocess import derive_fitness_metrics_from_canonical_ledger
from models import (
    EvidenceRecord,
    FAIRagroApplicationDataFitnessModel,
    RelatedResource,
    ReviewStatus,
    TuningParameterRecord,
    ValidationMetricRecord,
)
from quantitative_values import enrich_quantitative_values


REVIEW_SCHEMA_VERSION = "fairagro-dffp-review-v1.2"
COMPATIBLE_REVIEW_SCHEMA_VERSIONS = {"fairagro-dffp-review-v1", "fairagro-dffp-review-v1.1"}
MACHINE_SCHEMA_VERSION = "fairagro-dffp-v3.5.2"
COMPATIBLE_MACHINE_SCHEMA_VERSIONS = {
    "fairagro-dffp-v3.2.8",
    "fairagro-dffp-v3.4.0",
    "fairagro-dffp-v3.4.1",
    "fairagro-dffp-v3.5.0",
    "fairagro-dffp-v3.5.1",
    MACHINE_SCHEMA_VERSION,
}
MACHINE_EXTRACTION_PROMPT_VERSION = "extraction_v12"
COMPATIBLE_MACHINE_EXTRACTION_PROMPT_VERSIONS = {
    "extraction_v9",
    "extraction_v10",
    "extraction_v11",
    MACHINE_EXTRACTION_PROMPT_VERSION,
}
VALID_ACTIONS = {"verify", "correct", "reject", "defer"}


class ReviewWorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewPaths:
    review_dir: Path
    baseline_matrix: Path
    baseline_manifest: Path | None
    baseline_validation_report: Path | None
    queue: Path
    review_manifest: Path
    reviewed_matrix: Path
    publication_gate_report: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return _sha256_bytes(path.read_bytes())


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _slug(text: str, max_len: int = 44) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", _norm(text)).strip("-")
    return (out or "item")[:max_len].rstrip("-")


def _review_id(prefix: str, readable: str, identity: dict[str, Any]) -> str:
    digest = _sha256_json(identity)[:16]
    return f"{prefix}_{_slug(readable)}_{digest}"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive path
        raise ReviewWorkflowError(f"Could not read JSON {path}: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_single_record(matrix_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = _load_json(matrix_path)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ReviewWorkflowError("The application matrix must be a non-empty JSON list of record objects.")
    if len(payload) != 1:
        raise ReviewWorkflowError(
            "Human review currently expects exactly one matrix record per review workspace. "
            "Split multi-record matrices before review."
        )
    # Validate the immutable baseline against the frozen extraction model.
    try:
        FAIRagroApplicationDataFitnessModel.model_validate(payload[0])
    except ValidationError as exc:
        raise ReviewWorkflowError(f"Baseline matrix does not match the frozen extraction schema: {exc}") from exc
    return payload, payload[0]


def _pointer(parts: Iterable[str | int]) -> str:
    def esc(part: str | int) -> str:
        return str(part).replace("~", "~0").replace("/", "~1")
    return "/" + "/".join(esc(x) for x in parts)


def _is_evidence_dict(value: Any) -> bool:
    return isinstance(value, dict) and "claim" in value and "evidence_type" in value and "review_status" in value


def _evidence_identity(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim": _norm(ev.get("claim")),
        "source_item_id": _norm(ev.get("source_item_id")),
        "source_section": _norm(ev.get("source_section")),
        "source_location": _norm(ev.get("source_location")),
        "source_page": ev.get("source_page"),
        "source_text": _norm(ev.get("source_text")),
    }


def _metric_identity(metric: dict[str, Any]) -> dict[str, Any]:
    ev_ids = sorted(_sha256_json(_evidence_identity(x))[:12] for x in metric.get("evidence") or [] if isinstance(x, dict))
    return {
        "name": _norm(metric.get("name")),
        "context": _norm(metric.get("context")),
        "scope": _norm(metric.get("scope")),
        "scope_label": _norm(metric.get("scope_label")),
        "evaluation_support": _norm(metric.get("evaluation_support")),
        "evaluation_population": _norm(metric.get("evaluation_population")),
        "evidence": ev_ids,
    }


def _resource_identity(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _norm(resource.get("name")),
        "identifier": _norm(resource.get("identifier")),
        "resource_type": _norm(resource.get("resource_type")),
        "relation": _norm(resource.get("relation")),
        "version": _norm(resource.get("version")),
    }


def _parameter_identity(parameter: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _norm(parameter.get("name")),
        "scope": _norm(parameter.get("scope")),
        "scope_label": _norm(parameter.get("scope_label")),
        "value_or_summary": _norm(parameter.get("value_or_summary")),
    }


def _priority_for_path(kind: str, path: tuple[str | int, ...]) -> str:
    joined = ".".join(str(x) for x in path)
    if kind == "validation_metric":
        return "critical"
    if kind == "related_resource":
        return "high"
    if kind == "tuning_parameter":
        return "high"
    if any(
        token in joined
        for token in (
            "validation_and_diagnostics",
            "uncertainty_handling",
            "limitations_and_risks",
            "application_profile",
            "decision_risk_profile",
        )
    ):
        return "critical"
    if "document_metadata.related_resources" in joined or "reproducibility_and_fairness" in joined:
        return "high"
    return "normal"


def _item_title(kind: str, obj: dict[str, Any]) -> str:
    if kind == "validation_metric":
        target = obj.get("scope_label") or obj.get("scope") or "unspecified target"
        value = obj.get("value_or_summary")
        return f"{obj.get('name', 'metric')} — {target}" + (f" = {value}" if value not in (None, "") else "")
    if kind == "related_resource":
        return f"Related resource — {obj.get('name') or obj.get('identifier') or 'unnamed'}"
    if kind == "tuning_parameter":
        return f"Tuning parameter — {obj.get('name') or 'unnamed'}"
    return str(obj.get("claim") or "Evidence claim")


def _item_locator(kind: str, obj: dict[str, Any]) -> dict[str, Any] | None:
    if kind == "evidence":
        return {
            "section": obj.get("source_section"),
            "location": obj.get("source_location"),
            "page": obj.get("source_page"),
            "source_item_id": obj.get("source_item_id"),
            "source_modality": obj.get("source_modality"),
            "representation_method": obj.get("representation_method"),
        }
    evidence = obj.get("evidence") or []
    if evidence and isinstance(evidence[0], dict):
        return _item_locator("evidence", evidence[0])
    return None


def _canonical_parent_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    metrics = (((record.get("validation_and_diagnostics") or {}).get("validation_metrics")) or [])
    for i, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            continue
        identity = _metric_identity(metric)
        rid = _review_id("metric", metric.get("name") or "metric", identity)
        path = ("validation_and_diagnostics", "validation_metrics", i)
        items.append({
            "review_id": rid,
            "kind": "validation_metric",
            "json_pointer": _pointer((0, *path)),
            "priority": _priority_for_path("validation_metric", path),
            "required_for_publication": True,
            "title": _item_title("validation_metric", metric),
            "locator": _item_locator("validation_metric", metric),
            "snapshot": deepcopy(metric),
            "identity": identity,
            "parent_review_id": None,
        })

    resources = (((record.get("document_metadata") or {}).get("related_resources")) or [])
    for i, resource in enumerate(resources):
        if not isinstance(resource, dict):
            continue
        identity = _resource_identity(resource)
        rid = _review_id("resource", resource.get("name") or "resource", identity)
        path = ("document_metadata", "related_resources", i)
        items.append({
            "review_id": rid,
            "kind": "related_resource",
            "json_pointer": _pointer((0, *path)),
            "priority": _priority_for_path("related_resource", path),
            "required_for_publication": True,
            "title": _item_title("related_resource", resource),
            "locator": _item_locator("related_resource", resource),
            "snapshot": deepcopy(resource),
            "identity": identity,
            "parent_review_id": None,
        })

    parameters = (((record.get("validation_and_diagnostics") or {}).get("tuning_parameters")) or [])
    for i, parameter in enumerate(parameters):
        if not isinstance(parameter, dict):
            continue
        identity = _parameter_identity(parameter)
        rid = _review_id("parameter", parameter.get("name") or "parameter", identity)
        path = ("validation_and_diagnostics", "tuning_parameters", i)
        items.append({
            "review_id": rid,
            "kind": "tuning_parameter",
            "json_pointer": _pointer((0, *path)),
            "priority": _priority_for_path("tuning_parameter", path),
            "required_for_publication": True,
            "title": _item_title("tuning_parameter", parameter),
            "locator": _item_locator("tuning_parameter", parameter),
            "snapshot": deepcopy(parameter),
            "identity": identity,
            "parent_review_id": None,
        })
    return items


def _build_parent_lookup(parent_items: list[dict[str, Any]]) -> dict[str, str]:
    # Map a JSON pointer to the review ID of canonical parent records so nested
    # evidence can become not-applicable if its parent is rejected.
    return {item["json_pointer"]: item["review_id"] for item in parent_items}


def _iter_evidence(
    value: Any,
    path: tuple[str | int, ...] = (),
    *,
    parent_lookup: dict[str, str],
    parent_review_id: str | None = None,
):
    # Fitness metrics are deterministic mirrors of the canonical metric ledger;
    # reviewing their duplicate evidence would create duplicate human work.
    if len(path) >= 2 and path[:2] == ("outputs_and_fitness_indicators", "fitness_for_use_metrics"):
        return

    current_pointer = _pointer((0, *path)) if path else "/0"
    if current_pointer in parent_lookup:
        parent_review_id = parent_lookup[current_pointer]

    if _is_evidence_dict(value):
        yield path, value, parent_review_id
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_evidence(item, (*path, key), parent_lookup=parent_lookup, parent_review_id=parent_review_id)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _iter_evidence(item, (*path, i), parent_lookup=parent_lookup, parent_review_id=parent_review_id)


def build_review_queue(record: dict[str, Any], *, baseline_sha256: str) -> dict[str, Any]:
    parent_items = _canonical_parent_items(record)
    parent_lookup = _build_parent_lookup(parent_items)
    items = list(parent_items)
    evidence_item_by_id: dict[str, dict[str, Any]] = {}

    for path, evidence, parent_id in _iter_evidence(record, parent_lookup=parent_lookup):
        identity = _evidence_identity(evidence)
        rid = _review_id("evidence", evidence.get("claim") or "claim", identity)
        # A semantic duplicate elsewhere in the record is one scientific claim
        # to review once. If it occurs both inside and outside a reviewable
        # parent, do not make its applicability depend on rejecting one copy.
        if rid in evidence_item_by_id:
            existing = evidence_item_by_id[rid]
            if existing.get("parent_review_id") != parent_id:
                existing["parent_review_id"] = None
            existing.setdefault("duplicate_json_pointers", []).append(_pointer((0, *path)))
            continue
        item = {
            "review_id": rid,
            "kind": "evidence",
            "json_pointer": _pointer((0, *path)),
            "duplicate_json_pointers": [],
            "priority": _priority_for_path("evidence", path),
            "required_for_publication": True,
            "title": _item_title("evidence", evidence),
            "locator": _item_locator("evidence", evidence),
            "snapshot": deepcopy(evidence),
            "identity": identity,
            "parent_review_id": parent_id,
        }
        evidence_item_by_id[rid] = item
        items.append(item)

    priority_rank = {"critical": 0, "high": 1, "normal": 2}
    kind_rank = {"validation_metric": 0, "related_resource": 1, "tuning_parameter": 2, "evidence": 3}
    items.sort(key=lambda x: (priority_rank.get(x["priority"], 9), kind_rank.get(x["kind"], 9), x["review_id"]))
    return {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "baseline_sha256": baseline_sha256,
        "items": items,
    }


def _effective_decisions(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rid, history in (manifest.get("decision_history") or {}).items():
        if history:
            out[rid] = history[-1]
    return out


def apply_queue_status(queue: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    queue = deepcopy(queue)
    decisions = _effective_decisions(manifest)
    by_id = {x["review_id"]: x for x in queue.get("items", [])}

    counts = {"pending": 0, "verified": 0, "corrected": 0, "rejected": 0, "deferred": 0, "not_applicable": 0}
    required_remaining = 0
    for item in queue.get("items", []):
        parent_id = item.get("parent_review_id")
        if parent_id and (decisions.get(parent_id) or {}).get("action") == "reject":
            status = "not_applicable"
        else:
            action = (decisions.get(item["review_id"]) or {}).get("action")
            status = {
                "verify": "verified",
                "correct": "corrected",
                "reject": "rejected",
                "defer": "deferred",
            }.get(action, "pending")
        item["review_status"] = status
        item["latest_decision"] = decisions.get(item["review_id"])
        counts[status] += 1
        if item.get("required_for_publication") and status in {"pending", "deferred"}:
            required_remaining += 1

    queue["summary"] = {
        "total": len(queue.get("items", [])),
        **counts,
        "required_remaining": required_remaining,
        "publication_review_complete": required_remaining == 0,
    }
    return queue


def _review_paths(review_dir: Path, *, baseline_name: str = "application_matrix.machine.json") -> ReviewPaths:
    return ReviewPaths(
        review_dir=review_dir,
        baseline_matrix=review_dir / baseline_name,
        baseline_manifest=review_dir / "extraction_manifest.machine.json",
        baseline_validation_report=review_dir / "validation_report.machine.json",
        queue=review_dir / "review_queue.json",
        review_manifest=review_dir / "review_manifest.json",
        reviewed_matrix=review_dir / "application_matrix.reviewed.json",
        publication_gate_report=review_dir / "publication_gate.json",
    )


def initialize_review_workspace(
    matrix_path: str | Path,
    *,
    review_dir: str | Path,
    extraction_manifest_path: str | Path | None = None,
    validation_report_path: str | Path | None = None,
    reviewer: str | None = None,
) -> ReviewPaths:
    matrix_path = Path(matrix_path).resolve()
    review_dir = Path(review_dir).resolve()
    review_dir.mkdir(parents=True, exist_ok=True)
    paths = _review_paths(review_dir)

    payload, record = _load_single_record(matrix_path)
    baseline_bytes = matrix_path.read_bytes()
    baseline_sha = _sha256_bytes(baseline_bytes)

    # Immutable audit copies.  Refuse to silently overwrite a workspace that was
    # initialized from a different machine baseline.
    if paths.baseline_matrix.exists():
        existing_sha = _sha256_file(paths.baseline_matrix)
        if existing_sha != baseline_sha:
            raise ReviewWorkflowError(
                "Review workspace already contains a different machine baseline. "
                "Use a new review directory rather than overwriting review provenance."
            )
    else:
        paths.baseline_matrix.write_bytes(baseline_bytes)

    extraction_manifest = Path(extraction_manifest_path).resolve() if extraction_manifest_path else None
    validation_report = Path(validation_report_path).resolve() if validation_report_path else None
    machine_contract: dict[str, Any] = {}
    if extraction_manifest:
        extraction_payload = _load_json(extraction_manifest)
        release_config = extraction_payload.get("release_config") or {} if isinstance(extraction_payload, dict) else {}
        final_run = extraction_payload.get("final_structured_run") or extraction_payload.get("extraction_run") or {} if isinstance(extraction_payload, dict) else {}
        schema_version = release_config.get("schema_version") or final_run.get("schema_version")
        prompt_version = release_config.get("extraction_prompt_version") or final_run.get("extraction_prompt_version")
        machine_contract = {
            "schema_version": schema_version,
            "extraction_prompt_version": prompt_version,
            "system_prompt_version": release_config.get("system_prompt_version") or final_run.get("system_prompt_version"),
            "repair_prompt_version": release_config.get("repair_prompt_version"),
        }
        run_purpose = release_config.get("run_purpose") or "legacy_unknown"
        machine_contract["run_purpose"] = run_purpose
        if schema_version and schema_version not in COMPATIBLE_MACHINE_SCHEMA_VERSIONS:
            raise ReviewWorkflowError(
                f"Review expects a compatible machine schema {sorted(COMPATIBLE_MACHINE_SCHEMA_VERSIONS)!r}; "
                f"the supplied manifest reports {schema_version!r}."
            )
        if prompt_version and prompt_version not in COMPATIBLE_MACHINE_EXTRACTION_PROMPT_VERSIONS:
            raise ReviewWorkflowError(
                f"Review expects a compatible machine extraction prompt {sorted(COMPATIBLE_MACHINE_EXTRACTION_PROMPT_VERSIONS)!r}; "
                f"the supplied manifest reports {prompt_version!r}."
            )
        paths.baseline_manifest.write_bytes(extraction_manifest.read_bytes())
    if validation_report:
        paths.baseline_validation_report.write_bytes(validation_report.read_bytes())

    queue = build_review_queue(record, baseline_sha256=baseline_sha)
    manifest = {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "created_at_utc": _utc_now(),
        "updated_at_utc": _utc_now(),
        "machine_baseline": {
            "filename": matrix_path.name,
            "sha256": baseline_sha,
            "extraction_manifest_sha256": _sha256_file(extraction_manifest),
            "validation_report_sha256": _sha256_file(validation_report),
            "machine_contract": machine_contract,
        },
        "reviewer_default": reviewer,
        "decision_history": {},
        "source_publication_state": {
            "manuscript_frozen": False,
            "source_document_doi": record.get("document_metadata", {}).get("doi"),
            "source_document_identifier_checked": False,
        },
        "reviewed_matrix": None,
        "publication": None,
    }
    queue = apply_queue_status(queue, manifest)
    _write_json(paths.queue, queue)
    _write_json(paths.review_manifest, manifest)
    return paths


def _migrate_workspace_payloads(
    paths: ReviewPaths,
    payload: list[dict[str, Any]],
    queue: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Migrate v1/v1.1 review sidecars without touching the machine baseline.

    The queue is rebuilt from the immutable baseline so review IDs and dependency
    relationships are re-established by current deterministic code. Decision
    history is preserved exactly. Previously derived matrices/gate reports are
    invalidated because they may have been produced by older derivation logic.
    """
    manifest_version = manifest.get("review_schema_version")
    queue_version = queue.get("review_schema_version")
    if manifest_version == REVIEW_SCHEMA_VERSION and queue_version == REVIEW_SCHEMA_VERSION:
        return queue, manifest

    versions = {x for x in (manifest_version, queue_version) if x}
    if not versions:
        versions = {"fairagro-dffp-review-v1"}
    unsupported = versions - COMPATIBLE_REVIEW_SCHEMA_VERSIONS - {REVIEW_SCHEMA_VERSION}
    if unsupported:
        raise ReviewWorkflowError(
            "Review workspace uses unsupported review schema version(s): " + ", ".join(sorted(unsupported))
        )
    if manifest.get("publication"):
        raise ReviewWorkflowError(
            "This older review workspace already records a publication. Refusing automatic migration; "
            "create a new review workspace from the immutable machine baseline instead."
        )

    baseline_sha = (manifest.get("machine_baseline") or {}).get("sha256")
    rebuilt = build_review_queue(payload[0], baseline_sha256=baseline_sha)
    rebuilt_ids = {x.get("review_id") for x in rebuilt.get("items", [])}
    historical_ids = set((manifest.get("decision_history") or {}).keys())
    missing_ids = sorted(historical_ids - rebuilt_ids)
    if missing_ids:
        raise ReviewWorkflowError(
            "Cannot migrate review workspace because historical decision IDs are not present in the rebuilt queue: "
            + ", ".join(missing_ids[:8])
        )

    old_versions = sorted(versions)
    manifest["review_schema_version"] = REVIEW_SCHEMA_VERSION
    manifest.setdefault("workspace_migrations", []).append({
        "from_versions": old_versions,
        "to_version": REVIEW_SCHEMA_VERSION,
        "migrated_at_utc": _utc_now(),
        "reason": "Review-layer maintenance migration; immutable machine baseline and decision history preserved.",
    })
    manifest["reviewed_matrix"] = None
    manifest["publication"] = None
    manifest["updated_at_utc"] = _utc_now()
    rebuilt = apply_queue_status(rebuilt, manifest)

    # Derived artifacts are reproducible and must be regenerated with current
    # derivation semantics. The immutable machine baseline is never modified.
    for stale in (paths.reviewed_matrix, paths.publication_gate_report):
        if stale.exists():
            stale.unlink()
    _write_json(paths.review_manifest, manifest)
    _write_json(paths.queue, rebuilt)
    return rebuilt, manifest


def load_review_workspace(review_dir: str | Path) -> tuple[ReviewPaths, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    review_dir = Path(review_dir).resolve()
    paths = _review_paths(review_dir)
    for required in (paths.baseline_matrix, paths.queue, paths.review_manifest):
        if not required.exists():
            raise ReviewWorkflowError(f"Review workspace is incomplete; missing {required.name}")
    payload, record = _load_single_record(paths.baseline_matrix)
    queue = _load_json(paths.queue)
    manifest = _load_json(paths.review_manifest)
    expected = (manifest.get("machine_baseline") or {}).get("sha256")
    actual = _sha256_file(paths.baseline_matrix)
    if expected != actual:
        raise ReviewWorkflowError("Machine baseline hash mismatch. The immutable review baseline appears to have changed.")
    queue, manifest = _migrate_workspace_payloads(paths, payload, queue, manifest)
    return paths, payload, queue, manifest


def migrate_review_workspace(review_dir: str | Path) -> dict[str, Any]:
    """Explicitly load/migrate a workspace and report its current schema state."""
    paths, _, queue, manifest = load_review_workspace(review_dir)
    return {
        "review_dir": str(paths.review_dir),
        "review_schema_version": manifest.get("review_schema_version"),
        "queue_schema_version": queue.get("review_schema_version"),
        "workspace_migrations": manifest.get("workspace_migrations") or [],
        "reviewed_matrix": manifest.get("reviewed_matrix"),
    }


def _evidence_rejection_conflicts_for_item(
    queue: dict[str, Any],
    manifest: dict[str, Any],
    evidence_item: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return retained canonical parents that would lose all source evidence.

    Parent evidence is part of the scientific support contract even where the
    extraction schema happens to make it optional. Human review must not turn a
    source-grounded machine record into an unsupported retained parent merely by
    rejecting its last evidence child.
    """
    if evidence_item.get("kind") != "evidence":
        return []
    identity = evidence_item.get("identity") or {}
    decisions = _effective_decisions(manifest)
    conflicts: list[dict[str, Any]] = []
    for parent in queue.get("items", []):
        if parent.get("kind") not in {"validation_metric", "related_resource", "tuning_parameter"}:
            continue
        if (decisions.get(parent.get("review_id")) or {}).get("action") == "reject":
            continue
        evidence = (parent.get("snapshot") or {}).get("evidence") or []
        if not isinstance(evidence, list) or not evidence:
            continue
        matching = [
            ev for ev in evidence
            if isinstance(ev, dict) and _evidence_identity(ev) == identity
        ]
        if matching and len(evidence) - len(matching) < 1:
            conflicts.append({
                "parent_review_id": parent.get("review_id"),
                "parent_kind": parent.get("kind"),
                "parent_title": parent.get("title"),
            })
    return conflicts


def review_decision_conflicts(queue: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Find contradictory latest review decisions without changing the workspace."""
    decisions = _effective_decisions(manifest)
    conflicts: list[dict[str, Any]] = []
    for item in queue.get("items", []):
        decision = decisions.get(item.get("review_id")) or {}
        if item.get("kind") != "evidence" or decision.get("action") != "reject":
            continue
        parents = _evidence_rejection_conflicts_for_item(queue, manifest, item)
        if not parents:
            continue
        conflicts.append({
            "code": "REJECTED_LAST_PARENT_EVIDENCE",
            "review_id": item.get("review_id"),
            "title": item.get("title"),
            "parents": parents,
            "message": (
                "Rejected evidence is the only source evidence for one or more retained parent records. "
                "Reject the unsupported parent record instead, or change this evidence decision to "
                "Verify/Correct/Defer."
            ),
        })
    return conflicts


def review_conflicts(review_dir: str | Path) -> list[dict[str, Any]]:
    """Return actionable review-decision conflicts for an initialized workspace."""
    _, _, queue, manifest = load_review_workspace(review_dir)
    return review_decision_conflicts(queue, manifest)


def record_review_decision(
    review_dir: str | Path,
    *,
    review_id: str,
    action: str,
    reviewer: str | None = None,
    reason: str | None = None,
    patch: dict[str, Any] | None = None,
    source_checked: bool = True,
) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise ReviewWorkflowError(f"Unknown review action {action!r}; choose one of {sorted(VALID_ACTIONS)}")
    paths, _, queue, manifest = load_review_workspace(review_dir)
    item_map = {x["review_id"]: x for x in queue.get("items", [])}
    if review_id not in item_map:
        raise ReviewWorkflowError(f"Unknown review ID {review_id!r}")
    if action == "correct" and not isinstance(patch, dict):
        raise ReviewWorkflowError("A 'correct' decision requires a JSON object patch.")
    if action != "correct" and patch:
        raise ReviewWorkflowError("Only a 'correct' decision may include a patch.")

    item = item_map[review_id]
    normalized_reason = (reason or "").strip()
    if action in {"correct", "reject", "defer"} and not normalized_reason:
        raise ReviewWorkflowError(
            f"A meaningful review reason is required for {action!r} decisions. "
            "Verify may be recorded without a note."
        )
    if action == "correct":
        if not patch:
            raise ReviewWorkflowError("A 'correct' decision requires at least one field change.")
        candidate = deepcopy(item.get("snapshot") or {})
        _apply_patch(candidate, patch, kind=item.get("kind") or "record")
        if _canonical_json(candidate) == _canonical_json(item.get("snapshot") or {}):
            raise ReviewWorkflowError(
                "Correction patch is a no-op: it does not change the machine-baseline snapshot. "
                "Use Verify instead if the extracted record is already correct."
            )
    if action == "reject" and item.get("kind") == "evidence":
        conflicts = _evidence_rejection_conflicts_for_item(queue, manifest, item)
        if conflicts:
            labels = "; ".join(
                f"{x['parent_kind']}: {x['parent_title']} ({x['parent_review_id']})"
                for x in conflicts
            )
            raise ReviewWorkflowError(
                "Cannot reject this evidence because it is the only source evidence for retained parent record(s): "
                f"{labels}. If the parent claim/metric is unsupported, reject the parent record first. "
                "If the source evidence is basically valid but its wording or locator is wrong, correct this evidence instead."
            )

    event = {
        "action": action,
        "reviewer": reviewer or manifest.get("reviewer_default") or "unspecified_reviewer",
        "reviewed_at_utc": _utc_now(),
        "reason": normalized_reason or None,
        "source_checked": bool(source_checked),
        "patch": deepcopy(patch) if patch is not None else None,
        "baseline_snapshot_sha256": _sha256_json(item_map[review_id].get("snapshot")),
    }
    manifest.setdefault("decision_history", {}).setdefault(review_id, []).append(event)
    manifest["updated_at_utc"] = _utc_now()
    queue = apply_queue_status(queue, manifest)
    _write_json(paths.review_manifest, manifest)
    _write_json(paths.queue, queue)
    return event


def set_source_publication_state(
    review_dir: str | Path,
    *,
    manuscript_frozen: bool | None = None,
    source_document_doi: str | None = None,
    identifier_checked: bool | None = None,
) -> dict[str, Any]:
    paths, _, queue, manifest = load_review_workspace(review_dir)
    state = manifest.setdefault("source_publication_state", {})
    if manuscript_frozen is not None:
        state["manuscript_frozen"] = bool(manuscript_frozen)
    if source_document_doi is not None:
        state["source_document_doi"] = source_document_doi.strip() or None
    if identifier_checked is not None:
        state["source_document_identifier_checked"] = bool(identifier_checked)
    manifest["updated_at_utc"] = _utc_now()
    _write_json(paths.review_manifest, manifest)
    _write_json(paths.queue, apply_queue_status(queue, manifest))
    return deepcopy(state)


def _get_at(root: Any, path: tuple[str | int, ...]) -> Any:
    cur = root
    for part in path:
        cur = cur[part]
    return cur


def _find_index_by_identity(items: list[Any], identity_fn, identity: dict[str, Any]) -> int | None:
    for i, item in enumerate(items):
        if isinstance(item, dict) and identity_fn(item) == identity:
            return i
    return None


def _locate_parent_object(record: dict[str, Any], item: dict[str, Any]) -> tuple[list[Any], int] | None:
    kind = item["kind"]
    if kind == "validation_metric":
        arr = ((record.get("validation_and_diagnostics") or {}).get("validation_metrics") or [])
        idx = _find_index_by_identity(arr, _metric_identity, item["identity"])
        return (arr, idx) if idx is not None else None
    if kind == "related_resource":
        arr = ((record.get("document_metadata") or {}).get("related_resources") or [])
        idx = _find_index_by_identity(arr, _resource_identity, item["identity"])
        return (arr, idx) if idx is not None else None
    if kind == "tuning_parameter":
        arr = ((record.get("validation_and_diagnostics") or {}).get("tuning_parameters") or [])
        idx = _find_index_by_identity(arr, _parameter_identity, item["identity"])
        return (arr, idx) if idx is not None else None
    return None


def _walk_find_evidence_all(value: Any, identity: dict[str, Any], *, exclude_fitness_mirrors: bool = True) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if _is_evidence_dict(value) and _evidence_identity(value) == identity:
        return [value]
    if isinstance(value, dict):
        for key, item in value.items():
            if exclude_fitness_mirrors and key == "fitness_for_use_metrics":
                continue
            found.extend(_walk_find_evidence_all(item, identity, exclude_fitness_mirrors=exclude_fitness_mirrors))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_find_evidence_all(item, identity, exclude_fitness_mirrors=exclude_fitness_mirrors))
    return found


def _remove_evidence_by_identity(value: Any, identity: dict[str, Any], *, exclude_fitness_mirrors: bool = True) -> int:
    removed = 0
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if exclude_fitness_mirrors and key == "fitness_for_use_metrics":
                continue
            if isinstance(item, list):
                kept = []
                for child in item:
                    if _is_evidence_dict(child) and _evidence_identity(child) == identity:
                        removed += 1
                    else:
                        removed += _remove_evidence_by_identity(child, identity, exclude_fitness_mirrors=exclude_fitness_mirrors)
                        kept.append(child)
                value[key] = kept
            else:
                removed += _remove_evidence_by_identity(item, identity, exclude_fitness_mirrors=exclude_fitness_mirrors)
    elif isinstance(value, list):
        for child in value:
            removed += _remove_evidence_by_identity(child, identity, exclude_fitness_mirrors=exclude_fitness_mirrors)
    return removed


def _apply_patch(obj: dict[str, Any], patch: dict[str, Any], *, kind: str) -> None:
    patch = deepcopy(patch)
    if kind in {"validation_metric", "related_resource", "tuning_parameter"} and "evidence" in patch:
        raise ReviewWorkflowError(
            f"Corrections to {kind} may not replace its evidence list. Review/correct evidence records separately."
        )
    patch.pop("review_status", None)
    allowed_by_kind = {
        "validation_metric": set(ValidationMetricRecord.model_fields),
        "related_resource": set(RelatedResource.model_fields),
        "tuning_parameter": set(TuningParameterRecord.model_fields),
        "evidence": set(EvidenceRecord.model_fields),
    }
    allowed = allowed_by_kind.get(kind, set(obj))
    for key, value in patch.items():
        if key not in allowed:
            raise ReviewWorkflowError(
                f"Correction patch for {kind} contains unknown field {key!r}. "
                "The review layer does not extend the extraction schema."
            )
        obj[key] = value
    if kind in {"validation_metric", "tuning_parameter"} and not patch.get("quantitative_value"):
        if {"name", "value_or_summary", "unit"} & set(patch):
            obj.pop("quantitative_value", None)


def review_decision_quality_issues(queue: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return provenance-quality issues in the latest review decisions.

    Older v1/v1.1 workspaces remain derivable after migration, but publication is
    blocked until legacy Reject/Correct/Defer decisions have meaningful reasons
    and no latest correction is a no-op.
    """
    decisions = _effective_decisions(manifest)
    item_map = {x.get("review_id"): x for x in queue.get("items", [])}
    issues: list[dict[str, Any]] = []
    for rid, decision in decisions.items():
        action = decision.get("action")
        item = item_map.get(rid)
        if item is None:
            issues.append({
                "code": "UNKNOWN_DECISION_REVIEW_ID",
                "review_id": rid,
                "message": "Latest decision refers to a review ID not present in the current queue.",
            })
            continue
        if action in {"correct", "reject", "defer"} and not (decision.get("reason") or "").strip():
            issues.append({
                "code": "MISSING_REQUIRED_REASON",
                "review_id": rid,
                "title": item.get("title"),
                "action": action,
                "message": f"{action.title()} decisions require a meaningful review reason under {REVIEW_SCHEMA_VERSION}.",
            })
        if action == "correct":
            patch = decision.get("patch")
            if not isinstance(patch, dict) or not patch:
                issues.append({
                    "code": "EMPTY_CORRECTION_PATCH",
                    "review_id": rid,
                    "title": item.get("title"),
                    "message": "Correction decision has no field changes.",
                })
                continue
            try:
                candidate = deepcopy(item.get("snapshot") or {})
                _apply_patch(candidate, patch, kind=item.get("kind") or "record")
                if _canonical_json(candidate) == _canonical_json(item.get("snapshot") or {}):
                    issues.append({
                        "code": "NO_OP_CORRECTION",
                        "review_id": rid,
                        "title": item.get("title"),
                        "message": "Correction patch does not change the machine-baseline snapshot; use Verify instead.",
                    })
            except ReviewWorkflowError as exc:
                issues.append({
                    "code": "INVALID_CORRECTION_PATCH",
                    "review_id": rid,
                    "title": item.get("title"),
                    "message": str(exc),
                })
    return issues


def review_audit_issues(review_dir: str | Path) -> list[dict[str, Any]]:
    _, _, queue, manifest = load_review_workspace(review_dir)
    return review_decision_quality_issues(queue, manifest)


def derive_reviewed_matrix(review_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    paths, payload, queue, manifest = load_review_workspace(review_dir)
    queue = apply_queue_status(queue, manifest)
    record = deepcopy(payload[0])
    decisions = _effective_decisions(manifest)
    item_map = {x["review_id"]: x for x in queue.get("items", [])}

    conflicts = review_decision_conflicts(queue, manifest)
    if conflicts:
        details = []
        for conflict in conflicts:
            parents = ", ".join(
                f"{p['parent_title']} ({p['parent_review_id']})"
                for p in conflict.get("parents", [])
            )
            details.append(f"{conflict['review_id']} -> {parents}")
        raise ReviewWorkflowError(
            "Review decision conflict: rejecting evidence would leave retained parent record(s) without source evidence. "
            "Resolve the rejected evidence item by changing it to Verify/Correct/Defer, or reject the affected parent record first. "
            "Conflicts: " + " | ".join(details)
        )

    rejected_parents: set[str] = set()
    # Parent records are edited first. Evidence patches are deliberately kept
    # separate so parent corrections cannot silently rewrite reviewed evidence.
    for item in queue.get("items", []):
        if item["kind"] == "evidence":
            continue
        decision = decisions.get(item["review_id"])
        if not decision or decision.get("action") == "defer":
            continue
        loc = _locate_parent_object(record, item)
        if loc is None:
            raise ReviewWorkflowError(f"Could not locate baseline object for {item['review_id']} during derivation.")
        arr, idx = loc
        if decision["action"] == "reject":
            arr.pop(idx)
            rejected_parents.add(item["review_id"])
        elif decision["action"] == "correct":
            _apply_patch(arr[idx], decision.get("patch") or {}, kind=item["kind"])

    evidence_stats = {"verified": 0, "corrected": 0, "rejected": 0, "deferred": 0, "unreviewed": 0}
    any_correction = any((d.get("action") == "correct") for d in decisions.values())
    for item in queue.get("items", []):
        if item["kind"] != "evidence":
            continue
        if item.get("parent_review_id") in rejected_parents:
            continue
        decision = decisions.get(item["review_id"])
        action = decision.get("action") if decision else None
        identity = item["identity"]
        if action == "reject":
            _remove_evidence_by_identity(record, identity)
            evidence_stats["rejected"] += 1
            continue
        evidence_records = _walk_find_evidence_all(record, identity)
        if not evidence_records:
            raise ReviewWorkflowError(f"Could not locate evidence record {item['review_id']} during derivation.")
        for ev in evidence_records:
            if action == "verify":
                ev["review_status"] = "human_verified"
            elif action == "correct":
                _apply_patch(ev, decision.get("patch") or {}, kind="evidence")
                ev["review_status"] = "human_corrected"
            elif action == "defer":
                ev["review_status"] = "machine_extracted"
            else:
                ev["review_status"] = "machine_extracted"
        if action == "verify":
            evidence_stats["verified"] += 1
        elif action == "correct":
            evidence_stats["corrected"] += 1
        elif action == "defer":
            evidence_stats["deferred"] += 1
        else:
            evidence_stats["unreviewed"] += 1

    # Publication-state DOI is controlled separately from scientific review so
    # a DOI assigned to the frozen source document can be inserted without
    # rewriting the machine baseline or re-running the extractor.
    source_state = manifest.get("source_publication_state") or {}
    source_doi = (source_state.get("source_document_doi") or "").strip()
    if source_doi and source_state.get("source_document_identifier_checked") is True:
        record.setdefault("document_metadata", {})["doi"] = source_doi

    # Re-validate and rebuild deterministic metric mirrors from the edited
    # canonical ledger. This prevents rejected/corrected metrics from leaving
    # stale copies under outputs_and_fitness_indicators.
    try:
        model = FAIRagroApplicationDataFitnessModel.model_validate(record)
    except ValidationError as exc:
        raise ReviewWorkflowError(f"Human review decisions produced an invalid DFFP record: {exc}") from exc
    enrich_quantitative_values(model)
    derive_fitness_metrics_from_canonical_ledger(model, promote_legacy=False)

    # Extraction provenance remains the extraction provenance, but its review
    # status summarizes the human-review state of the currently derived record.
    remaining = queue.get("summary", {}).get("required_remaining", 0)
    if model.extraction_provenance is not None:
        if remaining == 0:
            model.extraction_provenance.review_status = ReviewStatus.human_corrected if any_correction else ReviewStatus.human_verified
        else:
            model.extraction_provenance.review_status = ReviewStatus.machine_extracted

    reviewed_payload = [model.model_dump(mode="json", by_alias=True, exclude_none=True)]
    _write_json(paths.reviewed_matrix, reviewed_payload)
    reviewed_sha = _sha256_file(paths.reviewed_matrix)
    manifest["reviewed_matrix"] = {
        "filename": paths.reviewed_matrix.name,
        "sha256": reviewed_sha,
        "derived_at_utc": _utc_now(),
        "baseline_sha256": (manifest.get("machine_baseline") or {}).get("sha256"),
        "review_summary": queue.get("summary"),
        "evidence_status_counts": evidence_stats,
    }
    manifest["updated_at_utc"] = _utc_now()
    _write_json(paths.review_manifest, manifest)
    _write_json(paths.queue, queue)
    return paths.reviewed_matrix, manifest["reviewed_matrix"]


def _validation_counts(report_path: Path | None) -> tuple[int | None, int | None]:
    if report_path is None or not report_path.exists():
        return None, None
    payload = _load_json(report_path)
    if not isinstance(payload, list):
        return None, None
    errors = sum(1 for x in payload if isinstance(x, dict) and x.get("severity") == "error")
    warnings = sum(1 for x in payload if isinstance(x, dict) and x.get("severity") == "warning")
    return errors, warnings


def publication_gate(review_dir: str | Path, *, require_source_doi: bool = True) -> dict[str, Any]:
    paths, _, queue, manifest = load_review_workspace(review_dir)
    queue = apply_queue_status(queue, manifest)
    errors, warnings = _validation_counts(paths.baseline_validation_report)
    state = manifest.get("source_publication_state") or {}
    machine_contract = ((manifest.get("machine_baseline") or {}).get("machine_contract") or {})
    run_purpose = machine_contract.get("run_purpose", "legacy_unknown")
    checks = [
        {
            "id": "run_not_marked_test",
            "passed": run_purpose != "test",
            "detail": f"run_purpose={run_purpose!r}",
        },
        {
            "id": "machine_validation_errors_zero",
            "passed": errors == 0,
            "detail": f"machine validation errors={errors!r}",
        },
        {
            "id": "machine_validation_warnings_zero",
            "passed": warnings == 0,
            "detail": f"machine validation warnings={warnings!r}",
        },
        {
            "id": "required_human_review_complete",
            "passed": queue.get("summary", {}).get("required_remaining") == 0,
            "detail": f"required_remaining={queue.get('summary', {}).get('required_remaining')}",
        },
        {
            "id": "review_decision_quality_issues_zero",
            "passed": len(review_decision_quality_issues(queue, manifest)) == 0,
            "detail": f"review_decision_quality_issues={len(review_decision_quality_issues(queue, manifest))}",
        },
        {
            "id": "manuscript_frozen",
            "passed": state.get("manuscript_frozen") is True,
            "detail": f"manuscript_frozen={state.get('manuscript_frozen')!r}",
        },
        {
            "id": "source_identifier_checked",
            "passed": state.get("source_document_identifier_checked") is True,
            "detail": f"source_document_identifier_checked={state.get('source_document_identifier_checked')!r}",
        },
    ]
    if require_source_doi:
        checks.append({
            "id": "source_document_doi_resolved",
            "passed": bool((state.get("source_document_doi") or "").strip()),
            "detail": f"source_document_doi={state.get('source_document_doi')!r}",
        })
    gate = {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "checked_at_utc": _utc_now(),
        "ready_for_publication": all(x["passed"] for x in checks),
        "checks": checks,
        "review_summary": queue.get("summary"),
        "machine_baseline_sha256": (manifest.get("machine_baseline") or {}).get("sha256"),
        "reviewed_matrix_sha256": (manifest.get("reviewed_matrix") or {}).get("sha256"),
    }
    _write_json(paths.publication_gate_report, gate)
    return gate


def publish_reviewed_matrix(
    review_dir: str | Path,
    *,
    output_path: str | Path,
    require_source_doi: bool = True,
) -> Path:
    paths, _, queue, manifest = load_review_workspace(review_dir)
    # Always re-derive immediately before publication so the final artifact
    # reflects the latest review decisions and source-publication state.
    derive_reviewed_matrix(review_dir)
    _, _, queue, manifest = load_review_workspace(review_dir)
    gate = publication_gate(review_dir, require_source_doi=require_source_doi)
    if not gate["ready_for_publication"]:
        failed = [x["id"] for x in gate["checks"] if not x["passed"]]
        raise ReviewWorkflowError("Publication gate failed: " + ", ".join(failed))
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(paths.reviewed_matrix.read_bytes())
    manifest["publication"] = {
        "filename": output_path.name,
        "sha256": _sha256_file(output_path),
        "published_at_utc": _utc_now(),
        "publication_gate_sha256": _sha256_file(paths.publication_gate_report),
    }
    manifest["updated_at_utc"] = _utc_now()
    _write_json(paths.review_manifest, manifest)
    return output_path


def review_status(review_dir: str | Path) -> dict[str, Any]:
    paths, _, queue, manifest = load_review_workspace(review_dir)
    queue = apply_queue_status(queue, manifest)
    _write_json(paths.queue, queue)
    return {
        "review_dir": str(paths.review_dir),
        "baseline_sha256": (manifest.get("machine_baseline") or {}).get("sha256"),
        "summary": queue.get("summary"),
        "source_publication_state": manifest.get("source_publication_state"),
        "decision_quality_issue_count": len(review_decision_quality_issues(queue, manifest)),
        "reviewed_matrix": manifest.get("reviewed_matrix"),
        "publication": manifest.get("publication"),
    }
