#!/usr/bin/env python3
"""Validate generic Evidence Graph question slices."""

from __future__ import annotations

import json
from pathlib import Path


def validate_file(payload: dict, graph: dict) -> dict:
    errors: list[str] = []
    if payload.get("schema") != "skill-lens.evidence-question-slices.v0.1":
        errors.append("invalid slice collection schema")
    if payload.get("caseId") != graph.get("caseId") or payload.get("revision") != graph.get("revision"):
        errors.append("slice collection identity differs from graph")
    evidence = {item["id"]: item for item in graph.get("evidence", [])}
    nodes = {item["id"]: item for item in graph.get("nodes", [])}
    edges = {item["id"]: item for item in graph.get("edges", [])}
    budget = payload.get("budgetChars")
    max_nodes = payload.get("maxNodes")
    if not isinstance(budget, int) or budget <= 0:
        errors.append("invalid collection budget")
    if not isinstance(max_nodes, int) or max_nodes <= 0:
        errors.append("invalid collection maxNodes")
    slices = payload.get("slices")
    if not isinstance(slices, list) or not slices:
        errors.append("slices must be a non-empty list")
        slices = []
    for index, record in enumerate(slices):
        item = record.get("slice", {}) if isinstance(record, dict) else {}
        prefix = f"slice {index}"
        if item.get("schema") != "skill-lens.evidence-question-slice.v0.1":
            errors.append(f"{prefix}: invalid slice schema")
        if item.get("caseId") != graph.get("caseId") or item.get("revision") != graph.get("revision"):
            errors.append(f"{prefix}: identity mismatch")
        selected = item.get("selected", [])
        selected_ids = set(item.get("selectedNodeIds", []))
        if len(selected) > max_nodes:
            errors.append(f"{prefix}: selected more than maxNodes")
        if item.get("usedChars", 0) > budget:
            errors.append(f"{prefix}: budget exceeded")
        actual_ids = set()
        for selected_item in selected:
            node_id = selected_item.get("nodeId")
            evidence_id = selected_item.get("evidenceId")
            actual_ids.add(node_id)
            node = nodes.get(node_id)
            ref = evidence.get(evidence_id)
            if not node or not ref:
                errors.append(f"{prefix}: selected node or Evidence is missing")
                continue
            if evidence_id not in node.get("evidence", []):
                errors.append(f"{prefix}: Evidence is not attached to selected node")
            if selected_item.get("quote") != ref.get("quote") or selected_item.get("source") != ref.get("source"):
                errors.append(f"{prefix}: selected citation differs from graph Evidence")
            if ref.get("source", {}).get("revision") != graph.get("revision"):
                errors.append(f"{prefix}: selected Evidence revision mismatch")
        if actual_ids != selected_ids:
            errors.append(f"{prefix}: selectedNodeIds differs from selected items")
        for edge in item.get("edges", []):
            if edge.get("from") not in selected_ids or edge.get("to") not in selected_ids:
                errors.append(f"{prefix}: edge endpoint outside selected nodes")
            if edge.get("id") not in edges:
                errors.append(f"{prefix}: selected edge missing from graph")
    return {"valid": not errors, "errors": errors}


def validate_path(path: Path, graph_path: Path) -> dict:
    return validate_file(json.loads(path.read_text(encoding="utf-8")), json.loads(graph_path.read_text(encoding="utf-8")))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=Path)
    parser.add_argument("graph", type=Path)
    args = parser.parse_args()
    result = validate_path(args.payload, args.graph)
    if not result["valid"]:
        raise SystemExit("invalid Evidence slices: " + "; ".join(result["errors"]))
    print(f"validated Evidence slices: {args.payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
