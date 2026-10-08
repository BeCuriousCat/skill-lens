#!/usr/bin/env python3
"""Validate provider GraphPatch structure and cross-references."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "skill-lens.graph-patch.v0.1.schema.json"


def validate_schema(data: dict, path: Path, schema_path: Path) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        # Keep validation runnable in the dependency-free provider environment.
        # The fallback covers the schema's required fields and primitive shapes;
        # referential integrity remains enforced below.
        if not isinstance(data, dict):
            raise ValueError(f"{path}: schema validation failed: root must be an object")
        required = {"schema", "provider", "caseId", "revision", "nodes", "edges", "evidence", "diagnostics"}
        if not required.issubset(data):
            raise ValueError(f"{path}: schema validation failed: missing required property")
        if data.get("schema") != "skill-lens.graph-patch.v0.1":
            raise ValueError(f"{path}: schema validation failed: unsupported schema")
        if not isinstance(data.get("provider"), dict) or not data["provider"].get("id") or not data["provider"].get("version"):
            raise ValueError(f"{path}: schema validation failed: provider requires id and version")
        for name in ("nodes", "edges", "evidence", "diagnostics"):
            if not isinstance(data.get(name), list):
                raise ValueError(f"{path}: schema validation failed: {name} must be an array")
        for index, node in enumerate(data["nodes"]):
            if not isinstance(node, dict) or not all(isinstance(node.get(key), str) and node[key] for key in ("id", "type", "label")):
                raise ValueError(f"{path}: schema validation failed at nodes[{index}]: id, type, and label are required")
            if not isinstance(node.get("evidence"), list) or not node["evidence"] or not all(isinstance(item, str) and item for item in node["evidence"]):
                raise ValueError(f"{path}: schema validation failed at nodes[{index}].evidence: non-empty string array required")
        for index, edge in enumerate(data["edges"]):
            if not isinstance(edge, dict) or not all(isinstance(edge.get(key), str) and edge[key] for key in ("id", "from", "to", "type")):
                raise ValueError(f"{path}: schema validation failed at edges[{index}]: id, from, to, and type are required")
        return
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda error: list(error.path))
    if errors:
        location = "".join(f"[{part!r}]" for part in errors[0].path)
        raise ValueError(f"{path}: schema validation failed at {location}: {errors[0].message}")


def validate(path: Path, schema_path: Path = SCHEMA_PATH) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_schema(data, path, schema_path)
    required = {"schema", "provider", "caseId", "revision", "nodes", "edges", "evidence", "diagnostics"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"{path}: missing {sorted(missing)}")
    if data["schema"] != "skill-lens.graph-patch.v0.1":
        raise ValueError(f"{path}: unsupported schema")
    if len(data["revision"]) != 40 or any(c not in "0123456789abcdef" for c in data["revision"]):
        raise ValueError(f"{path}: invalid revision")
    if not data["provider"].get("id") or not data["provider"].get("version"):
        raise ValueError(f"{path}: provider id/version required")
    node_ids = [node.get("id") for node in data["nodes"]]
    edge_ids = [edge.get("id") for edge in data["edges"]]
    evidence_ids = [item.get("id") for item in data["evidence"]]
    if None in node_ids or len(node_ids) != len(set(node_ids)):
        raise ValueError(f"{path}: node IDs must be present and unique")
    if None in evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError(f"{path}: evidence IDs must be present and unique")
    if None in edge_ids or len(edge_ids) != len(set(edge_ids)):
        raise ValueError(f"{path}: edge IDs must be present and unique")
    node_set, edge_set, evidence_set = set(node_ids), set(edge_ids), set(evidence_ids)
    for node in data["nodes"]:
        if not node.get("type") or not node.get("label") or not node.get("evidence"):
            raise ValueError(f"{path}: schema/semantic validation failed: node missing type, label, or evidence")
        if set(node["evidence"]) - evidence_set:
            raise ValueError(f"{path}: node references missing evidence")
    for edge in data["edges"]:
        if not edge.get("id") or edge.get("from") not in node_set or edge.get("to") not in node_set or not edge.get("type"):
            raise ValueError(f"{path}: edge references missing node or has invalid fields")
        if set(edge.get("evidence", [])) - evidence_set:
            raise ValueError(f"{path}: edge references missing evidence")
    for item in data["evidence"]:
        if item.get("source", {}).get("revision") != data["revision"]:
            raise ValueError(f"{path}: evidence revision mismatch")
    for diagnostic in data["diagnostics"]:
        if set(diagnostic.get("nodeIds", [])) - node_set:
            raise ValueError(f"{path}: diagnostic references missing node")
        if set(diagnostic.get("edgeIds", [])) - edge_set:
            raise ValueError(f"{path}: diagnostic references missing edge")


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research/results")
    schema_path = Path(sys.argv[2]) if len(sys.argv) > 2 else SCHEMA_PATH
    paths = sorted(root.glob("**/*.graph-patch.json"))
    if not paths:
        raise SystemExit("no GraphPatch files found")
    for path in paths:
        validate(path, schema_path)
        print(f"{path}: ok")
    print(f"validated {len(paths)} GraphPatch files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
