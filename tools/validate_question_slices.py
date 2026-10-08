#!/usr/bin/env python3
"""Validate query-scoped slices against the GraphPatch they reference."""

from __future__ import annotations


def validate(slices: list[dict], graph: dict) -> dict:
    errors: list[str] = []
    case_id = graph.get("caseId")
    revision = graph.get("revision")
    nodes = {item.get("id"): item for item in graph.get("nodes", [])}
    evidence = {item.get("id"): item for item in graph.get("evidence", [])}
    for index, record in enumerate(slices):
        prefix = f"slices[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if record.get("caseId", case_id) != case_id:
            errors.append(f"{prefix} caseId mismatch")
        if record.get("revision", revision) != revision:
            errors.append(f"{prefix} revision mismatch")
        selected = (record.get("slice") or {}).get("selected", [])
        if not isinstance(selected, list):
            errors.append(f"{prefix}.slice.selected must be an array")
            continue
        for selected_index, item in enumerate(selected):
            item_prefix = f"{prefix}.slice.selected[{selected_index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_prefix} must be an object")
                continue
            node_id = item.get("nodeId")
            evidence_id = item.get("evidenceId")
            if node_id not in nodes:
                errors.append(f"{item_prefix} references unknown nodeId {node_id!r}")
                continue
            if evidence_id not in evidence:
                errors.append(f"{item_prefix} references unknown evidenceId {evidence_id!r}")
                continue
            if evidence_id not in nodes[node_id].get("evidence", []):
                errors.append(f"{item_prefix} evidenceId is not attached to nodeId")
            source = evidence[evidence_id].get("source", {})
            if source.get("revision") != revision:
                errors.append(f"{item_prefix} evidence revision mismatch")
            source_ref = item.get("source", {})
            if source_ref and source_ref != source:
                errors.append(f"{item_prefix} source reference differs from graph evidence")
    return {"valid": not errors, "errors": errors, "sliceCount": len(slices)}


def validate_file(payload: dict, graph: dict) -> dict:
    errors = []
    if payload.get("schema") != "skill-lens.question-slices.v0.1":
        errors.append("unsupported question-slices schema")
    if payload.get("caseId") != graph.get("caseId"):
        errors.append("question-slices caseId mismatch")
    if payload.get("revision") != graph.get("revision"):
        errors.append("question-slices revision mismatch")
    result = validate(payload.get("slices", []), graph)
    result["errors"] = errors + result["errors"]
    result["valid"] = not result["errors"]
    return result
