#!/usr/bin/env python3
"""Validate semantic projection references against a canonical Evidence Graph."""

import argparse
import json
from pathlib import Path


def validate_projection(projection, graph):
    errors = []
    if not isinstance(projection, dict):
        return {"valid": False, "caseId": None, "objectCount": 0, "errors": ["projection must be an object"]}
    if not isinstance(graph, dict):
        return {"valid": False, "caseId": projection.get("caseId"), "objectCount": 0, "errors": ["graph must be an object"]}
    if set(projection) - {"schema", "caseId", "revision", "analysisRun", "objects", "provenance"}:
        errors.append("projection contains unsupported properties")
    if projection.get("schema") != "skill-lens.semantic-projection.v0.1":
        errors.append("schema mismatch")
    if projection.get("caseId") != graph.get("caseId"):
        errors.append("caseId mismatch")
    if projection.get("revision") != graph.get("revision"):
        errors.append("revision mismatch")
    if not isinstance(projection.get("caseId"), str) or not projection.get("caseId"):
        errors.append("missing caseId")
    revision = projection.get("revision")
    if not isinstance(revision, str) or len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        errors.append("revision must be a 40-character lowercase hex SHA")
    if "provenance" in projection:
        provenance = projection.get("provenance")
        if not isinstance(provenance, dict):
            errors.append("provenance must be an object")
        else:
            if set(provenance) - {"runId", "source", "generatedAtUtc"}:
                errors.append("provenance contains unsupported properties")
            if not isinstance(provenance.get("runId"), str) or not provenance["runId"]:
                errors.append("provenance.runId must be a non-empty string")
            if provenance.get("source") not in {"model", "external-program", "fixture"}:
                errors.append("provenance.source must be model, external-program, or fixture")
            if not isinstance(provenance.get("generatedAtUtc"), str) or not provenance["generatedAtUtc"].endswith("Z"):
                errors.append("provenance.generatedAtUtc must be a UTC string ending in Z")
    objects = projection.get("objects")
    if not isinstance(objects, list) or not objects:
        errors.append("projection must contain at least one object")
        objects = []
    raw_nodes = graph.get("nodes", [])
    if not isinstance(raw_nodes, list):
        errors.append("graph nodes must be an array")
        raw_nodes = []
    node_ids = {node["id"] for node in raw_nodes if isinstance(node, dict) and isinstance(node.get("id"), str)}
    object_ids = set()
    allowed = {"Capability", "Component", "Scenario", "Ownership", "Limitation", "Uncertainty"}
    for obj in objects:
        if not isinstance(obj, dict):
            errors.append("projection object must be an object")
            continue
        for required in ("id", "type", "label", "summary", "evidenceNodeIds"):
            if required not in obj:
                errors.append(f"object missing {required}")
        if set(obj) - {"id", "type", "label", "summary", "evidenceNodeIds"}:
            errors.append(f"object contains unsupported properties: {obj.get('id')}")
        if not isinstance(obj.get("id"), str) or not obj.get("id"):
            errors.append("object id must be a non-empty string")
        if not isinstance(obj.get("label"), str) or not obj.get("label"):
            errors.append(f"object label must be non-empty: {obj.get('id')}")
        if not isinstance(obj.get("summary"), str) or not obj.get("summary"):
            errors.append(f"object summary must be non-empty: {obj.get('id')}")
        if isinstance(obj.get("id"), str):
            if obj["id"] in object_ids:
                errors.append(f"duplicate object id: {obj['id']}")
            object_ids.add(obj["id"])
        if not isinstance(obj.get("type"), str) or obj["type"] not in allowed:
            errors.append(f"unsupported object type: {obj.get('type')}")
        refs = obj.get("evidenceNodeIds", [])
        if not isinstance(refs, list):
            errors.append(f"evidenceNodeIds must be an array: {obj.get('id')}")
            refs = []
        if not refs:
            errors.append(f"object has no evidence: {obj.get('id')}")
        for ref in refs:
            if not isinstance(ref, str):
                errors.append(f"evidenceNodeIds entries must be strings: {obj.get('id')}")
            elif ref not in node_ids:
                errors.append(f"unknown evidence node {ref} in {obj.get('id')}")
    return {"valid": not errors, "caseId": projection.get("caseId"), "objectCount": len(objects), "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("projection", type=Path)
    parser.add_argument("graph", type=Path)
    args = parser.parse_args()
    projection = json.loads(args.projection.read_text())
    graph = json.loads(args.graph.read_text())
    result = validate_projection(projection, graph)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
