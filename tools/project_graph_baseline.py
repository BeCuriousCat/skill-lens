#!/usr/bin/env python3
"""Create a deliberately simple, evidence-preserving projection baseline."""

import argparse
import json
import re
from pathlib import Path


TYPE_MAP = {
    "Capability": "Capability",
    "Component": "Component",
    "Scenario": "Scenario",
    "Uncertainty": "Uncertainty",
    "Limitation": "Limitation",
}


def _source_evidence(node, evidence_by_id):
    """Render a small, source-linked quote block without adding interpretation."""
    entries = []
    for evidence_id in node.get("evidence", []):
        evidence = evidence_by_id.get(evidence_id)
        if not isinstance(evidence, dict):
            continue
        quote = re.sub(r"\s+", " ", str(evidence.get("quote", ""))).strip()
        if not quote:
            continue
        source = evidence.get("source", {})
        location = ":".join(str(source.get(key, "")) for key in ("path", "lines") if source.get(key))
        if len(quote) > 180:
            quote = quote[:177].rstrip() + "..."
        entries.append(f'"{quote}"' + (f" ({location})" if location else ""))
        if len(entries) == 3:
            break
    return entries


def project(graph):
    objects = []
    evidence_by_id = {
        item.get("id"): item
        for item in graph.get("evidence", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for node in graph.get("nodes", []):
        output_type = TYPE_MAP.get(node.get("type"))
        if output_type is None:
            continue
        attrs = node.get("attributes", {})
        detail = "; ".join(f"{key}={value}" for key, value in sorted(attrs.items()))
        summary = f"Static evidence node: {node.get('label', '')}."
        source_evidence = _source_evidence(node, evidence_by_id)
        if source_evidence:
            summary += " Source quotes: " + " | ".join(source_evidence) + "."
        if detail:
            summary += f" Recorded attributes: {detail}."
        objects.append({
            "id": f"baseline:{node['id']}",
            "type": output_type,
            "label": node.get("label", node["id"]),
            "summary": summary,
            "evidenceNodeIds": [node["id"]],
        })
    artifact = next((node for node in graph.get("nodes", []) if node.get("type") == "Artifact"), None)
    if artifact and any(node.get("type") == "Component" and node.get("attributes", {}).get("file") is True for node in graph.get("nodes", [])):
        objects.append({
            "id": f"baseline:inventory:{artifact['id']}",
            "type": "Component",
            "label": f"component inventory for {artifact.get('label', artifact['id'])}",
            "summary": "Static component inventory for the artifact; file presence does not establish execution or ownership.",
            "evidenceNodeIds": [artifact["id"]],
        })
    if not objects:
        artifact = next((node for node in graph.get("nodes", []) if node.get("type") == "Artifact"), None)
        if artifact:
            objects.append({
                "id": f"baseline:coverage-gap:{artifact['id']}",
                "type": "Uncertainty",
                "label": "No supported semantic facts were extracted",
                "summary": "The Provider returned an artifact inventory but no supported Capability, Component, or Scenario facts; further behavior remains unknown.",
                "evidenceNodeIds": [artifact["id"]],
            })
    return {
        "schema": "skill-lens.semantic-projection.v0.1",
        "caseId": graph["caseId"],
        "revision": graph["revision"],
        "objects": objects,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("graph", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(project(json.loads(args.graph.read_text())), ensure_ascii=False, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
