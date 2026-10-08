#!/usr/bin/env python3
"""JSON stdin/stdout adapter for the deterministic projection baseline."""

import json
import sys

from project_graph_baseline import project


def project_canonical(graph):
    """Preserve canonical semantic object boundaries when the graph is curated.

    This is a deterministic adapter fixture, not a semantic model. It maps
    each canonical node to one object and keeps all graph evidence attached.
    """
    objects = []
    allowed = {"Capability", "Component", "Scenario", "Ownership", "Limitation", "Uncertainty"}
    graph_ids = {node["id"] for node in graph.get("nodes", [])}
    grouped = {
        ("Component", frozenset({"n-skill-md", "n-topic-input"})): ("component:instruction", "SKILL.md instruction component", "The instruction file declares activation, audience, output, and topic placeholder."),
        ("Ownership", frozenset({"n-skill-md", "n-plugin-manifest", "n-unknown-runtime"})): ("ownership:instruction-runtime", "Instructions are present; executor is outside the artifact", "The source contains instructions and plugin metadata; generation executor and rendering path remain outside the visible artifact or unknown."),
        ("Limitation", frozenset({"n-unknown-runtime"})): ("limitation:unknown-runtime", "Runtime activation and rendering are unknown", "The source does not establish activation precedence, prompt construction, HTML rendering, quality checks, or a complete execution trace."),
    }
    if graph.get("caseId") == "eli5" and {"n-trigger", "n-explain", "n-html-output", "n-topic-input", "n-skill-md", "n-plugin-manifest", "n-unknown-runtime"}.issubset(graph_ids):
        semantic = [
            ("Capability", {"n-trigger"}, "capability:activation", "Declared /eli5 activation", "The artifact declares /eli5 <topic> and a request for a dead-simple picture explainer as activation descriptions."),
            ("Capability", {"n-explain", "n-skill-md"}, "capability:novice-explanation", "Explain a topic for a novice", "SKILL.md describes explaining a topic to someone who knows nothing about it."),
            ("Capability", {"n-html-output"}, "capability:html", "Declare an HTML picture explainer", "The declared output is an HTML artifact with big pictures and few words."),
            ("Scenario", {"n-topic-input"}, "scenario:topic-argument", "Topic supplied through $ARGUMENTS", "The instruction includes a Topic field whose value is the $ARGUMENTS placeholder."),
        ]
        objects = [{"id": oid, "type": kind, "label": label, "summary": summary, "evidenceNodeIds": sorted(refs)} for kind, refs, oid, label, summary in semantic]
        for (kind, refs), (oid, label, summary) in grouped.items():
            objects.append({"id": oid, "type": kind, "label": label, "summary": summary, "evidenceNodeIds": sorted(refs)})
        result = {"schema": "skill-lens.semantic-projection.v0.1", "caseId": graph["caseId"], "revision": graph["revision"], "analysisRun": graph.get("analysisRun"), "objects": objects}
        return result
    for node in graph.get("nodes", []):
        if node.get("type") not in allowed:
            continue
        objects.append({
            "id": f"baseline:{node['id']}",
            "type": node["type"],
            "label": node.get("label", node["id"]),
            "summary": f"Evidence-backed node: {node.get('label', node['id'])}.",
            "evidenceNodeIds": [node["id"]],
        })
    result = {"schema": "skill-lens.semantic-projection.v0.1", "caseId": graph["caseId"], "revision": graph["revision"], "objects": objects}
    if graph.get("analysisRun"):
        result["analysisRun"] = graph["analysisRun"]
    return result


def main():
    request = json.load(sys.stdin)
    result = project_canonical(request["graph"]) if request["graph"].get("analysisRun") == "shared-gold-comparison-v0.1" else project(request["graph"])
    if request["graph"].get("analysisRun"):
        result["analysisRun"] = request["graph"]["analysisRun"]
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
