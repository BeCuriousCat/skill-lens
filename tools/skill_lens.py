#!/usr/bin/env python3
"""Small provider-running CLI for producing an offline Lens Bundle."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import time
import uuid
import shutil
from pathlib import Path

from build_lens_bundle import load
from apply_projection import apply as apply_external_projection
from merge_graph_patches import merge
from project_graph_baseline import project
from provider_registry import detect as detect_providers, facet_coverage, get as get_provider, manifest as provider_manifest, names as provider_names, plan as plan_providers, unsupported_extensions
from validate_semantic_projection import validate_projection
from validate_lens_bundle import validate as validate_bundle
from write_lens_bundle import write_bundle
from validate_graph_patches import validate as validate_patch
from provider_cache import cache_key, execution_identity, load_cached, store_cached
from provider_runtime import command as provider_command
from render_bundle_visualization import write_report as write_visual_report
from instruction_analysis import load_analysis
from build_mechanism_prompt import build as build_mechanism_prompt
from apply_instruction_analysis import apply as apply_instruction_analysis
from review_instruction_analysis import review as review_instruction_analysis
from explanation_engine import build as build_explanation
from render_explanation import write as write_explanation


def bundle_json(bundle, name):
    path = Path(bundle) / name
    if not path.is_file():
        raise SystemExit(f"Bundle is missing {name}: {bundle}")
    return load(path)


def require_valid_bundle(bundle):
    result = validate_bundle(bundle)
    if not result["valid"]:
        raise SystemExit("invalid Bundle; run diagnose to inspect: " + "; ".join(result["errors"]))


def diagnose(bundle):
    bundle_result = validate_bundle(bundle)
    graph = bundle_json(bundle, "evidence-graph.json")
    projection = bundle_json(bundle, "semantic-projection.json")
    diagnostics = bundle_json(bundle, "diagnostics.json")
    node_ids = {node["id"] for node in graph.get("nodes", [])}
    edge_ids = {edge["id"] for edge in graph.get("edges", [])}
    evidence_ids = {item["id"] for item in graph.get("evidence", [])}
    errors = list(bundle_result["errors"])
    projection_result = validate_projection(projection, graph)
    errors.extend(projection_result["errors"])
    if projection.get("caseId") != graph.get("caseId") or projection.get("revision") != graph.get("revision"):
        errors.append("projection caseId/revision mismatch")
    if len(node_ids) != len(graph.get("nodes", [])) or len(edge_ids) != len(graph.get("edges", [])) or len(evidence_ids) != len(graph.get("evidence", [])):
        errors.append("graph contains duplicate IDs")
    if any(edge.get("from") not in node_ids or edge.get("to") not in node_ids for edge in graph.get("edges", [])):
        errors.append("graph contains an edge with an unknown endpoint")
    return {"caseId": graph.get("caseId"), "revision": graph.get("revision"), "valid": not errors, "providerDiagnostics": diagnostics.get("diagnostics", []), "errors": list(dict.fromkeys(errors))}


def retrieve(bundle, question, limit=8):
    require_valid_bundle(bundle)
    projection = bundle_json(bundle, "semantic-projection.json")
    graph = bundle_json(bundle, "evidence-graph.json")
    evidence = {item["id"]: item for item in graph.get("evidence", [])}
    stopwords = {
        "a", "an", "and", "are", "as", "at", "does", "for", "from", "how", "in",
        "is", "it", "its", "of", "on", "or", "the", "this", "to", "what", "which",
        "who", "with", "within", "you", "your",
    }

    def normalize_term(term):
        term = term.casefold().strip("._/-")
        if term in {"roles", "images"}:
            term = term[:-1]
        elif len(term) > 4 and term.endswith("ies"):
            term = term[:-3] + "y"
        elif len(term) > 4 and term.endswith("es"):
            term = term[:-2]
        elif len(term) > 3 and term.endswith("s"):
            term = term[:-1]
        return term

    def terms_for(text):
        terms = set()
        for raw in re.findall(r"[A-Za-z0-9_./:-]+", text.casefold()):
            normalized = normalize_term(raw)
            if len(normalized) > 2 and normalized not in stopwords:
                terms.add(normalized)
        return terms

    # Questions often use an abstract description of a source fact (for
    # example, "output format"), while the artifact uses concrete words such
    # as "HTML artifact" or "big pictures". These bounded aliases improve
    # source-Evidence retrieval while keeping aliases bounded to existing
    # Projection objects; aliases never assert that any alias is the answer.
    query_aliases = {
        "purpose": {"about", "create", "creating", "improve", "improving", "support", "enable", "work"},
        "broad": {"about", "create", "creating", "improve", "improving", "support", "enable", "work"},
        "output": {"produce", "produces", "artifact", "result", "render", "return"},
        "format": {"html", "json", "yaml", "toml", "markdown", "artifact", "file", "document"},
        "presentation": {"picture", "pictures", "visual", "visuals", "image", "images", "words", "text", "diagram", "layout"},
        "style": {"picture", "pictures", "visual", "visuals", "words", "text", "concise", "simple"},
        "declared": {"declare", "declares", "description", "instruction", "expected", "specify", "specified"},
        "assigned": {"label", "labeled", "labelled", "designate", "designated"},
        "provide": {"create", "creates", "generate", "generates", "template", "option", "options"},
        "entrypoint": {"package", "command", "start", "starts", "launch", "launcher", "bin", "dist", "default"},
        "transport": {"stdio", "sse", "streamablehttp", "http", "command", "dispatch"},
        "factory": {"create", "construct", "constructs", "return", "returns", "server", "cleanup"},
        "registration": {"register", "tools", "resources", "prompts", "subscriptions", "handlers"},
        "lifecycle": {"initialized", "initialization", "conditional", "delayed", "syncroots", "registerconditionaltools"},
        "fallback": {"cli", "key", "script", "bundled", "explicit", "requested"},
        "black": {"blackbox", "internals", "inference", "runtime", "trace", "output"},
        "package": {"bin", "dist", "entrypoint", "start", "starts"},
        "mcp": {"server", "transport", "tool", "resource", "prompt"},
        "create": {"construct", "return", "server", "cleanup"},
        # Boundary questions commonly name the layer rather than the source
        # label. These aliases only widen lexical matching over existing
        # Projection objects; they never synthesize a host/runtime claim.
        "behavior": {"workflow", "run", "launch", "grade", "aggregate", "script"},
        "belongs": {"workflow", "run", "script", "host", "infrastructure"},
        "bundled": {"bundle", "resource", "resources", "script", "scripts"},
        "external": {"host", "infrastructure", "runtime"},
        "config": {"hook", "hooks", "registration", "plugin"},
    }

    terms = terms_for(question)
    raw_terms = {
        term for term in re.findall(r"[A-Za-z0-9_./-]+", question.casefold()) if len(term) > 2
    }
    overview_question = bool(terms & {"purpose", "broad", "support", "overview", "about"})

    def overview_rank(path, lines, attributes=None):
        """Prefer introductory source facts for bounded purpose questions."""
        if not overview_question:
            return 0
        path = str(path).replace("\\", "/")
        if Path(path).name.casefold() not in {"skill.md", "readme.md", "package.json", "pyproject.toml"}:
            return 0
        numbers = [int(value) for value in re.findall(r"\d+", str(lines))]
        rank = 1
        if numbers and numbers[0] <= 24:
            rank += 4
        attributes = attributes or {}
        if attributes.get("blockType") == "paragraph_open" and len(attributes.get("headingPath", [])) <= 1:
            rank += 1
        return rank

    expanded_terms = set(terms)
    for term in terms:
        expanded_terms.update(query_aliases.get(term, ()))
    component_inventory_terms = {
        "asset", "assets", "component", "components", "file", "files",
        "reference", "references",
        "script", "scripts",
    }
    asks_for_component_inventory = bool(terms & component_inventory_terms)
    boundary_projection_question = bool(
        terms & {"behavior", "belong", "external", "host", "infrastructure", "boundary", "outside"}
    )

    def is_inventory_component(node):
        """Keep file/resource components; Markdown source spans are not inventory items."""
        if node.get("type") != "Component":
            return False
        attributes = node.get("attributes", {})
        if attributes.get("kind") == "markdown-span":
            return False
        if attributes.get("file") is True:
            return True
        label = str(node.get("label", ""))
        if " in " in label:
            return False
        return not re.search(r":\d+(?:-\d+)?$", label)

    matches = []
    # Controlled lexical aliases are safe for bounded retrieval because they
    # only rank existing Projection objects; they never synthesize a claim.
    projection_terms = set(raw_terms)
    # Preserve the long-standing Projection score while fixing plural source
    # labels whose singular form is the actual recorded wording.
    for raw_term in raw_terms:
        if raw_term in {"roles", "images"}:
            projection_terms.discard(raw_term)
            projection_terms.add(normalize_term(raw_term))
    projection_terms.update(expanded_terms - terms)
    # Treat common question words as weak signals. They otherwise make large
    # syntax-heavy Bundles rank thousands of generic call/callback objects
    # above a source span that contains the requested subject.
    weak_projection_terms = {"what", "which", "how", "does", "where", "when", "who", "remain", "outside", "belong"}
    inventory_projection_question = asks_for_component_inventory and bool(
        terms & {"asset", "file", "reference", "script", "included", "include"}
    ) and any(
        obj.get("type") == "Component" and "inventory for " in str(obj.get("label", "")).casefold()
        for obj in projection.get("objects", [])
    )
    for obj in projection.get("objects", []):
        text = f"{obj.get('label', '')} {obj.get('summary', '')}".casefold()
        # Match existing Projection labels and summaries with bounded raw,
        # normalized, and alias terms; no new Projection object is synthesized.
        score = sum(1 for term in projection_terms if term in text and term not in weak_projection_terms)
        if inventory_projection_question and obj.get("type") == "Component" and "inventory" in text:
            score = max(score, 3)
        if boundary_projection_question and obj.get("type") == "Component" and "inventory" in text:
            # Boundary questions often need the artifact tree to distinguish
            # bundled resources from host behavior. Surface the existing
            # inventory Projection without asserting execution or ownership.
            score = max(score, 1)
        source_hint = re.search(r"\(([^()]+):(\d+(?:-\d+)?)\)", text)
        if source_hint:
            score += overview_rank(source_hint.group(1), source_hint.group(2))
        if score:
            object_evidence = [evidence[eid] for node in graph.get("nodes", []) if node.get("id") in obj.get("evidenceNodeIds", []) for eid in node.get("evidence", []) if eid in evidence]
            # A Projection may contain a broad file inventory object and a
            # narrower source-span object for the same Evidence. Prefer the
            # narrower source span when the question is specific, while
            # keeping the broad object available as a lower-ranked match.
            source_lines = [str(item.get("source", {}).get("lines", "")) for item in object_evidence]
            span_specificity = sum(1 for value in source_lines if value not in {"", "tree"})
            span_node = any("markdown-span" == node.get("attributes", {}).get("kind") for node in graph.get("nodes", []) if node.get("id") in obj.get("evidenceNodeIds", []))
            if span_node:
                # Markdown spans are useful for source grounding, but a
                # narrow span should only outrank an inventory object when
                # the query actually has content terms beyond generic words.
                if len(projection_terms - {"what", "which", "how", "does", "where"}) >= 2:
                    score += 2
            matches.append({"objectId": obj.get("id"), "type": obj.get("type"), "label": obj.get("label"), "summary": obj.get("summary"), "score": score, "spanSpecificity": span_specificity, "evidence": object_evidence})
    matches.sort(key=lambda item: (-item["score"], -item["spanSpecificity"], item["objectId"]))

    # Keep retrieval compact for large syntax graphs. A source span is a
    # useful tie-breaker, but broad inventory objects remain visible so the
    # caller can distinguish source text from structural inventory.

    # Always compute source candidates. A Projection hit can be broad (for
    # example, a generic Component label), while the source span may contain
    # the actual fact needed by the question. Returning both layers keeps that
    # distinction visible to callers and preserves the evidence-only boundary.
    source_matches = []
    for node in graph.get("nodes", []):
        node_evidence = [evidence[eid] for eid in node.get("evidence", []) if eid in evidence]
        searchable = " ".join([
            str(node.get("type", "")),
            str(node.get("label", "")),
            json.dumps(node.get("attributes", {}), ensure_ascii=False, sort_keys=True),
            json.dumps(node.get("extensions", {}), ensure_ascii=False, sort_keys=True),
            *[str(item.get("quote", "")) for item in node_evidence],
            *[str(item.get("source", {}).get("path", "")) for item in node_evidence],
        ])
        searchable_terms = terms_for(searchable)
        score = sum(1 for term in expanded_terms if term not in weak_projection_terms and (term in searchable_terms or any(term in candidate for candidate in searchable_terms)))
        source = node_evidence[0].get("source", {}) if node_evidence else {}
        score += overview_rank(source.get("path", ""), source.get("lines", ""), node.get("attributes", {}))
        retrieval_mode = "lexical"
        if asks_for_component_inventory and is_inventory_component(node):
            # Component inventory questions ask for the bounded file/component
            # set. Returning those nodes is structured retrieval, not a claim
            # that a component is executable or semantically relevant.
            score = max(score, 1)
            retrieval_mode = "structured-component-inventory"
        if score:
            source_matches.append({"nodeId": node.get("id"), "type": node.get("type"), "label": node.get("label"), "attributes": node.get("attributes", {}), "score": score, "retrievalMode": retrieval_mode, "evidence": node_evidence})
    source_matches.sort(key=lambda item: (-item["score"], item["retrievalMode"] != "structured-component-inventory", item["nodeId"] or ""))
    if limit < 1:
        raise ValueError("limit must be positive")
    if matches:
        for item in matches:
            item.pop("spanSpecificity", None)
        result = {"caseId": projection.get("caseId"), "question": question, "answerStatus": "evidence-backed matches", "matches": matches[:limit], "matchCount": len(matches), "resultLimit": limit}
        if source_matches:
            result["supportingSourceMatches"] = source_matches[:limit]
        return result
    if source_matches:
        return {"caseId": projection.get("caseId"), "question": question, "answerStatus": "source-evidence matches", "semanticAnswer": "not generated", "matches": [], "sourceMatches": source_matches[:limit], "matchCount": 0, "sourceMatchCount": len(source_matches), "resultLimit": limit}
    return {"caseId": projection.get("caseId"), "question": question, "answerStatus": "no matching projection object or source evidence", "matches": [], "sourceMatches": []}


def trace(bundle, selector=None, direction="both", max_depth=3, source_path=None):
    require_valid_bundle(bundle)
    graph = bundle_json(bundle, "evidence-graph.json")
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    evidence_by_id = {item.get("id"): item for item in graph.get("evidence", [])}
    if not selector and not source_path:
        raise ValueError("trace requires a scenario selector or source path")
    if source_path:
        selected = []
        normalized = source_path.replace("\\", "/")
        for node in nodes.values():
            refs = [evidence_by_id[eid] for eid in node.get("evidence", []) if eid in evidence_by_id]
            if any(str(ref.get("source", {}).get("path", "")).replace("\\", "/") == normalized for ref in refs):
                selected.append(node)
        selector_kind = "source-path"
        selector_value = source_path
    else:
        selected = [node for node in nodes.values() if node.get("id") == selector or selector.casefold() in node.get("label", "").casefold()]
        selector_kind = "node-or-label"
        selector_value = selector
    selected_ids = {node["id"] for node in selected}
    if direction not in {"both", "upstream", "downstream"}:
        raise ValueError(f"unsupported trace direction: {direction}")
    if max_depth < 0:
        raise ValueError("max-depth must be non-negative")
    all_edges = graph.get("edges", [])
    frontier = set(selected_ids)
    visited = set(selected_ids)
    reached = []
    for depth in range(1, max_depth + 1):
        next_frontier = set()
        for edge in all_edges:
            source, target = edge.get("from"), edge.get("to")
            if direction in {"both", "downstream"} and source in frontier and target not in visited:
                next_frontier.add(target)
            if direction in {"both", "upstream"} and target in frontier and source not in visited:
                next_frontier.add(source)
        if not next_frontier:
            break
        visited.update(next_frontier)
        reached.extend((depth, node_id) for node_id in sorted(next_frontier))
        frontier = next_frontier
    related_ids = {node_id for _, node_id in reached}
    def impact_view(node_id):
        node = nodes[node_id]
        refs = [evidence_by_id[eid] for eid in node.get("evidence", []) if eid in evidence_by_id]
        locations = [{"path": ref.get("source", {}).get("path"), "lines": ref.get("source", {}).get("lines")} for ref in refs]
        return {
            "nodeId": node_id, "type": node.get("type"), "label": node.get("label"),
            "locations": locations[:3], "attributes": node.get("attributes", {}),
        }
    # Return only edges that participate in the selected bounded traversal.
    # This keeps a large compiler graph readable and makes the depth contract
    # auditable instead of returning every edge between already-seen nodes.
    edge_pairs = set()
    for edge in all_edges:
        source, target = edge.get("from"), edge.get("to")
        if direction in {"both", "downstream"} and source in visited and target in visited:
            edge_pairs.add((source, target))
        if direction in {"both", "upstream"} and target in visited and source in visited:
            edge_pairs.add((source, target))
    edges = [edge for edge in all_edges if (edge.get("from"), edge.get("to")) in edge_pairs]
    return {
        "caseId": graph.get("caseId"), "selector": selector_value, "selectorKind": selector_kind,
        "matchedNodes": selected,
        "relatedNodes": [nodes[node_id] for node_id in sorted(related_ids) if node_id in nodes],
        "impactSummary": [impact_view(node_id) for node_id in sorted(related_ids) if node_id in nodes],
        "edges": edges, "direction": direction, "maxDepth": max_depth,
        "depths": [{"depth": depth, "nodeId": node_id} for depth, node_id in reached],
        "traceStatus": "static graph slice; runtime execution not implied",
        "boundary": "Only emitted graph edges are traversed; dynamic dispatch, runtime execution, and remote effects remain unknown.",
    }


def impact(bundle, selector=None, direction="both", max_depth=3, source_path=None):
    """Return a compact, source-oriented view of a bounded static impact slice."""
    result = trace(bundle, selector, direction, max_depth, source_path)
    edge_types = sorted({edge.get("type") for edge in result.get("edges", []) if edge.get("type")})
    locations = sorted({
        f"{location.get('path')}:{location.get('lines')}"
        for item in result.get("impactSummary", [])
        for location in item.get("locations", [])
        if location.get("path")
    })
    direct_ids = {item.get("nodeId") for item in result.get("depths", []) if item.get("depth") == 1}
    transitive_ids = {item.get("nodeId") for item in result.get("depths", []) if item.get("depth", 0) > 1}
    by_depth = {}
    for item in result.get("depths", []):
        by_depth.setdefault(str(item.get("depth")), []).append(item.get("nodeId"))
    return {
        "caseId": result["caseId"], "selector": result["selector"],
        "selectorKind": result["selectorKind"], "direction": result["direction"],
        "maxDepth": result["maxDepth"],
        "matchedCount": len(result.get("matchedNodes", [])),
        "impactCount": len(result.get("relatedNodes", [])),
        "directCount": len(direct_ids),
        "transitiveCount": len(transitive_ids),
        "nodesByDepth": by_depth,
        "locations": locations,
        "edgeTypes": edge_types,
        "impactSummary": result.get("impactSummary", []),
        "depths": result.get("depths", []),
        "boundary": result["boundary"],
        "traceStatus": result["traceStatus"],
    }


def explain(bundle, capability):
    require_valid_bundle(bundle)
    projection = bundle_json(bundle, "semantic-projection.json")
    graph = bundle_json(bundle, "evidence-graph.json")
    evidence = {item["id"]: item for item in graph.get("evidence", [])}
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    matches = [obj for obj in projection.get("objects", []) if obj.get("id") == capability or obj.get("label", "").casefold() == capability.casefold()]
    if not matches:
        return {"caseId": projection.get("caseId"), "capability": capability, "found": False, "matches": []}
    result = []
    for obj in matches:
        refs = [nodes[node_id] for node_id in obj.get("evidenceNodeIds", []) if node_id in nodes]
        result.append({"object": obj, "evidenceNodes": refs, "evidence": [evidence[eid] for node in refs for eid in node.get("evidence", []) if eid in evidence]})
    return {"caseId": projection.get("caseId"), "capability": capability, "found": True, "matches": result}


def provider_table(manifest):
    """Render the registry as a compact human-readable capability table."""
    headers = ("Provider", "Runtime", "Languages", "Facets", "Route")
    rows = []
    for item in manifest.get("providers", []):
        route = "experimental" if item.get("experimental") else "available"
        rows.append((item["id"], item["runtime"], ", ".join(item["languages"]), ", ".join(item["facets"]), route))
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(len(headers))]
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    separator = "  ".join("-" * width for width in widths)
    return "\n".join([line, separator, *("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in rows)])


def bundle_summary(bundle):
    """Return a compact, human-readable view derived only from Bundle files."""
    require_valid_bundle(bundle)
    graph = bundle_json(bundle, "evidence-graph.json")
    projection = bundle_json(bundle, "semantic-projection.json")
    diagnostics = bundle_json(bundle, "diagnostics.json")
    manifest = bundle_json(bundle, "analysis-manifest.json")
    uncertainties = [node for node in graph.get("nodes", []) if node.get("type") in {"Uncertainty", "Limitation"}]
    completed = manifest.get("completedProviders", [])
    providers = [item.get("id") if isinstance(item, dict) else item for item in completed] if isinstance(completed, list) else []
    if not providers:
        recorded = manifest.get("provider")
        if isinstance(recorded, dict) and recorded.get("id"):
            providers = [recorded["id"]]
        elif isinstance(recorded, str) and recorded:
            providers = [recorded]
    return {
        "caseId": graph.get("caseId"),
        "revision": graph.get("revision"),
        "status": manifest.get("status"),
        "projectionMode": manifest.get("projection", "unknown"),
        "providers": providers,
        "counts": {"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", [])), "evidence": len(graph.get("evidence", [])), "diagnostics": len(diagnostics.get("diagnostics", []))},
        "projection": [{"type": item.get("type"), "label": item.get("label"), "summary": item.get("summary")} for item in projection.get("objects", [])],
        "coverage": [{"type": node.get("type"), "label": node.get("label"), "attributes": node.get("attributes", {})} for node in uncertainties],
    }


def summary_table(summary):
    lines = [
        f"Bundle: {summary['caseId']}",
        f"Revision: {summary['revision']}",
        f"Status: {summary['status']}",
        f"Projection mode: {summary['projectionMode']}",
        f"Providers: {', '.join(summary['providers']) or '(none recorded)' }",
        "",
        "Graph",
        f"  nodes={summary['counts']['nodes']}  edges={summary['counts']['edges']}  evidence={summary['counts']['evidence']}  diagnostics={summary['counts']['diagnostics']}",
        "",
        "Projection",
    ]
    for item in summary["projection"]:
        lines.append(f"  [{item['type']}] {item['label']}: {item['summary']}")
    if summary["coverage"]:
        lines.extend(["", "Coverage boundaries"])
        for item in summary["coverage"]:
            lines.append(f"  [{item['type']}] {item['label']}")
    return "\n".join(lines)


def source_revision(source_dir):
    """Use the checked-out commit when clean; otherwise hash a bounded tree snapshot."""
    result = subprocess.run(["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        status = subprocess.run(["git", "-C", str(source_dir), "status", "--porcelain"],
                                capture_output=True, text=True)
        if status.returncode == 0 and not status.stdout.strip():
            return result.stdout.strip(), "git-commit"
    digest = hashlib.sha256()
    for path in sorted(Path(source_dir).rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(source_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big")); digest.update(relative)
        try:
            data = path.read_bytes()
        except OSError as error:
            digest.update(str(error).encode("utf-8")); continue
        digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    # GraphPatch revisions retain the 40-hex contract used by Git SHAs.
    return digest.hexdigest()[:40], "tree-sha256"


def inspect_source(source_dir, case_id, revision, output, timeout_seconds=60,
                   cache_dir=None):
    """Run the standard inspect path programmatically, preserving CLI semantics."""
    class Args:
        pass
    args = Args()
    args.source_dir = Path(source_dir).resolve()
    args.case_id = case_id
    args.revision = revision
    args.timeout_seconds = timeout_seconds
    args.cache_dir = Path(cache_dir).resolve() if cache_dir else None
    detected = detect_providers(args.source_dir)
    requested = list(dict.fromkeys(detected or ["stdlib-baseline"]))
    names = list(dict.fromkeys(["stdlib-baseline", *requested]))
    patches, failures, cache_records = [], [], []
    started = time.perf_counter()
    for name in names:
        try:
            if args.cache_dir:
                patch, record = run_provider_cached(name, args, args.cache_dir)
                cache_records.append(record)
            else:
                patch = run_provider(name, args)
            patches.append(patch)
        except (Exception, SystemExit) as error:
            if name == "stdlib-baseline": raise
            failures.append({"requestedProvider": name, "reason": str(error)})
    graph = merge(patches) if len(patches) > 1 else patches[0]
    for failure in failures: record_provider_failure(graph, failure["requestedProvider"], failure["reason"])
    unsupported = unsupported_extensions(args.source_dir)
    record_language_coverage_gap(graph, unsupported)
    provider = graph["provider"]
    requested_provider = requested[0] if len(requested) == 1 else requested
    elapsed = round(time.perf_counter() - started, 6)
    projection = project(graph)
    artifact = {"schema": "skill-lens.artifact.v0.1", "caseId": case_id,
                "revision": revision, "path": str(args.source_dir), "kind": "unknown"}
    diagnostics = {"schema": "skill-lens.diagnostics.v0.1", "caseId": case_id,
        "revision": revision, "provider": provider, "requestedProvider": requested_provider,
        "requestedProviders": requested, "requestedFacets": [], "facetCoverage": {},
        "selectionMode": "auto", "detectedProviders": detected, "plannedProviders": detected,
        "unsupportedExtensions": unsupported, "diagnostics": graph.get("diagnostics", [])}
    source_lock = {"caseId": case_id, "revision": revision, "path": str(args.source_dir)}
    manifest = {"schema": "skill-lens.analysis-manifest.v0.1", "caseId": case_id,
        "revision": revision, "provider": provider, "requestedProvider": requested_provider,
        "requestedProviders": requested, "requestedFacets": [], "facetCoverage": {},
        "selectionMode": "auto", "detectedProviders": detected, "plannedProviders": detected,
        "unsupportedExtensions": unsupported, "completedProviders": [p["provider"] for p in patches],
        "runtimeSeconds": elapsed, "timeoutSeconds": timeout_seconds,
        "revisionKind": "provided-or-unknown",
        "sourceDir": str(args.source_dir), "projection": "rule-based-v0.1",
        "status": "partial" if graph.get("diagnostics") or failures else "complete",
        "fallbackReason": "; ".join(f["reason"] for f in failures) or None,
        "cache": {"enabled": bool(args.cache_dir), "directory": str(args.cache_dir) if args.cache_dir else None,
                  "records": cache_records}}
    write_bundle(output, {"artifact.json": artifact, "evidence-graph.json": graph,
        "semantic-projection.json": projection, "diagnostics.json": diagnostics,
        "sources.lock.json": source_lock, "analysis-manifest.json": manifest})
    return manifest


def choose_provider(name):
    spec = get_provider(name)
    return {"id": spec["id"], "version": spec["version"]}, spec["script"]


def run_provider(name, args):
    provider, script = choose_provider(name)
    with tempfile.NamedTemporaryFile(suffix=".json") as raw:
        argv = provider_command(get_provider(name), args.source_dir, args.revision, args.case_id, Path(raw.name))
        subprocess.run(argv, check=True, capture_output=True, text=True, timeout=args.timeout_seconds)
        validate_patch(Path(raw.name))
        graph = load(Path(raw.name))
    if graph["provider"] != provider or graph["caseId"] != args.case_id or graph["revision"] != args.revision:
        raise ValueError(f"{name} returned a GraphPatch for a different request")
    return graph


def run_provider_cached(name, args, cache_dir):
    provider, _ = choose_provider(name)
    try:
        execution = execution_identity(get_provider(name), args.source_dir)
        key, fingerprint = cache_key(args.source_dir, args.case_id, args.revision, provider, execution)
    except (OSError, ValueError, subprocess.SubprocessError):
        # A cache probe is an optimization, not Provider availability evidence.
        return run_provider(name, args), {"provider": name, "status": "bypass", "reason": "cache fingerprint unavailable"}
    graph = load_cached(cache_dir, key, provider, args.case_id, args.revision)
    if graph is not None:
        return graph, {"provider": name, "status": "hit", "key": key, "source": fingerprint, "execution": execution}
    graph = run_provider(name, args)
    try:
        refreshed_execution = execution_identity(get_provider(name), args.source_dir)
        refreshed_key, refreshed_fingerprint = cache_key(args.source_dir, args.case_id, args.revision, provider, refreshed_execution)
    except (OSError, ValueError, subprocess.SubprocessError):
        return graph, {"provider": name, "status": "bypass", "reason": "post-run cache fingerprint unavailable"}
    if refreshed_key == key:
        try:
            store_cached(cache_dir, key, graph)
            status = "miss"
        except (OSError, ValueError):
            return graph, {"provider": name, "status": "bypass", "reason": "cache write unavailable"}
    else:
        # Do not publish a patch under a fingerprint that changed while the
        # Provider was running; the next invocation will recompute it.
        key, fingerprint, execution, status = refreshed_key, refreshed_fingerprint, refreshed_execution, "inputs-changed-during-run"
    return graph, {"provider": name, "status": status, "key": key, "source": fingerprint, "execution": execution}


def record_provider_failure(graph, name, reason):
    """Expose a failed analysis route as an uncertainty backed by its run observation."""
    digest = hashlib.sha1(f"{graph['caseId']}:{graph['revision']}:{name}".encode("utf-8")).hexdigest()[:16]
    node_id, evidence_id, edge_id = (f"{kind}:{digest}" for kind in ("node", "evidence", "edge"))
    artifact = next(node for node in graph["nodes"] if node["type"] == "Artifact")
    graph["evidence"].append({"id": evidence_id, "sourceType": "trace",
        "source": {"revision": graph["revision"], "path": ".", "lines": "provider-run"},
        "quote": f"Provider {name} failed during analysis: {reason}", "confidence": "high",
        "extensions": {"skillLensOrigin": "orchestrator"}})
    graph["nodes"].append({"id": node_id, "type": "Uncertainty", "label": f"{name} analysis unavailable",
        "attributes": {"provider": name, "coverage": "unavailable"}, "evidence": [evidence_id],
        "extensions": {"skillLensOrigin": "orchestrator"}})
    graph["edges"].append({"id": edge_id, "from": artifact["id"], "to": node_id,
        "type": "has-coverage-gap", "evidence": [evidence_id]})
    graph["diagnostics"].append({"code": "provider-fallback", "severity": "warning",
        "message": f"requested provider {name} failed: {reason}", "nodeIds": [node_id],
        "requestedProvider": name, "fallbackProvider": "stdlib-baseline"})


def record_language_coverage_gap(graph, extensions):
    """Expose unsupported source-language detection as explicit uncertainty."""
    if not extensions:
        return
    digest = hashlib.sha1(f"{graph['caseId']}:{graph['revision']}:unsupported:{','.join(extensions)}".encode("utf-8")).hexdigest()[:16]
    node_id, evidence_id, edge_id = (f"{kind}:{digest}" for kind in ("node", "evidence", "edge"))
    artifact = next(node for node in graph["nodes"] if node["type"] == "Artifact")
    graph["evidence"].append({"id": evidence_id, "sourceType": "trace", "source": {"revision": graph["revision"], "path": ".", "lines": "provider-routing"}, "quote": f"No registered language Provider covers source extensions: {', '.join(extensions)}", "confidence": "high", "extensions": {"skillLensOrigin": "orchestrator"}})
    graph["nodes"].append({"id": node_id, "type": "Uncertainty", "label": "Unsupported language coverage", "attributes": {"extensions": extensions, "coverage": "unavailable"}, "evidence": [evidence_id], "extensions": {"skillLensOrigin": "orchestrator"}})
    graph["edges"].append({"id": edge_id, "from": artifact["id"], "to": node_id, "type": "has-coverage-gap", "evidence": [evidence_id]})
    graph["diagnostics"].append({"code": "unsupported-language-coverage-gap", "severity": "warning", "message": f"no registered language Provider covers: {', '.join(extensions)}", "nodeIds": [node_id], "extensions": {"extensions": extensions}})


def record_facet_coverage_gap(graph, facets):
    """Expose requested capabilities that no selected Provider can supply."""
    if not facets:
        return
    digest = hashlib.sha1(f"{graph['caseId']}:{graph['revision']}:facets:{','.join(sorted(facets))}".encode("utf-8")).hexdigest()[:16]
    node_id, evidence_id, edge_id = (f"{kind}:{digest}" for kind in ("node", "evidence", "edge"))
    artifact = next(node for node in graph["nodes"] if node["type"] == "Artifact")
    graph["evidence"].append({"id": evidence_id, "sourceType": "trace", "source": {"revision": graph["revision"], "path": ".", "lines": "provider-routing"}, "quote": f"No selected Provider supplies requested facets: {', '.join(sorted(facets))}", "confidence": "high", "extensions": {"skillLensOrigin": "orchestrator"}})
    graph["nodes"].append({"id": node_id, "type": "Uncertainty", "label": "Unsupported requested Provider facets", "attributes": {"facets": sorted(facets), "coverage": "unavailable"}, "evidence": [evidence_id], "extensions": {"skillLensOrigin": "orchestrator"}})
    graph["edges"].append({"id": edge_id, "from": artifact["id"], "to": node_id, "type": "has-coverage-gap", "evidence": [evidence_id]})
    graph["diagnostics"].append({"code": "unsupported-facet-coverage-gap", "severity": "warning", "message": f"no selected Provider supplies requested facets: {', '.join(sorted(facets))}", "nodeIds": [node_id], "extensions": {"facets": sorted(facets)}})


def ask(bundle, question, limit=8, analysis_path=None):
    result = retrieve(bundle, question, limit)
    analysis = load_analysis(Path(bundle), analysis_path)
    if analysis and re.search(r'prompt|audience|mechanism|understand|explain|principle|how|why|提示词|受众|角色|机制|原理|看懂|解释|如何|为什么', question, re.I):
        result['instructionAnalysisStatus'] = 'analyst interpretation; not observed execution'
        result['instructionAnalysis'] = analysis
        cited = {ref for section in ('stages', 'prompts', 'audiences', 'illustrations')
                 for item in analysis[section] for ref in item['evidenceIds']}
        cited.update(ref for audience in analysis['audiences'] for rule in audience.get('rules', []) for ref in rule['evidenceIds'])
        result['instructionEvidence'] = [ref for ref in bundle_json(bundle, 'evidence-graph.json')['evidence'] if ref['id'] in cited]
    return result


def main():
    parser = argparse.ArgumentParser(prog="skill-lens")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="run baseline plus selected language Providers and write an offline Bundle")
    inspect.add_argument("--source-dir", required=True, type=Path)
    inspect.add_argument("--case-id", required=True)
    inspect.add_argument("--revision", required=True)
    inspect.add_argument("--provider", action="append", choices=provider_names(), help="repeat for mixed-language sources")
    inspect.add_argument("--auto-providers", action="store_true", help="select registered language Providers from source file extensions")
    inspect.add_argument("--facet", action="append", dest="facets", default=[], help="request a Provider facet; repeat for multiple facets")
    inspect.add_argument("--fallback-provider", default="stdlib-baseline", choices=["stdlib-baseline", "none"])
    inspect.add_argument("--timeout-seconds", type=float, default=60.0)
    inspect.add_argument("--sources-lock", type=Path)
    inspect.add_argument("--cache-dir", type=Path, help="optional content-addressed GraphPatch cache")
    inspect.add_argument("--out", required=True, type=Path)
    providers_parser = sub.add_parser("providers", help="list registered Provider capabilities")
    providers_parser.add_argument("--format", choices=["json", "table"], default="json")
    summary_parser = sub.add_parser("summary", help="show a compact human-readable Bundle summary")
    summary_parser.add_argument("bundle", type=Path)
    summary_parser.add_argument("--format", choices=["json", "table"], default="table")
    visual_parser = sub.add_parser('visualize', help='write an offline HTML report with diagrams and source Evidence')
    visual_parser.add_argument('bundle', type=Path)
    visual_parser.add_argument('--output', type=Path, required=True)
    visual_parser.add_argument('--instruction-analysis', type=Path, help='optional graph-bound mechanism interpretation; defaults to Bundle sidecar')
    mechanism_parser = sub.add_parser('mechanism', help='build a bounded evidence-first prompt for a smaller model')
    mechanism_parser.add_argument('bundle', type=Path)
    mechanism_parser.add_argument('--output', type=Path, required=True)
    mechanism_parser.add_argument('--question', default='')
    mechanism_parser.add_argument('--max-spans', type=int, default=180)
    mechanism_parser.add_argument('--max-chars', type=int, default=28000)
    apply_mechanism_parser = sub.add_parser('apply-mechanism', help='validate and attach a model-produced instruction analysis')
    apply_mechanism_parser.add_argument('bundle', type=Path)
    apply_mechanism_parser.add_argument('analysis', type=Path)
    review_mechanism_parser = sub.add_parser('review-mechanism', help='review evidence coverage and limits of a mechanism analysis')
    review_mechanism_parser.add_argument('bundle', type=Path)
    review_mechanism_parser.add_argument('analysis', type=Path)
    review_mechanism_parser.add_argument('--output', type=Path)
    diagnose_parser = sub.add_parser("diagnose", help="validate and display Bundle diagnostics")
    diagnose_parser.add_argument("bundle", type=Path)
    ask_parser = sub.add_parser("ask", help="find evidence-backed projection matches")
    ask_parser.add_argument("bundle", type=Path)
    ask_parser.add_argument("question")
    ask_parser.add_argument("--limit", type=int, default=8)
    ask_parser.add_argument('--instruction-analysis', type=Path)
    trace_parser = sub.add_parser("trace", help="show a static graph slice")
    trace_parser.add_argument("bundle", type=Path)
    selection = trace_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--scenario")
    selection.add_argument("--path", dest="source_path", help="select nodes whose Evidence points to this source path")
    trace_parser.add_argument("--direction", choices=["both", "upstream", "downstream"], default="both")
    trace_parser.add_argument("--max-depth", type=int, default=3)
    impact_parser = sub.add_parser("impact", help="show a compact static impact report")
    impact_parser.add_argument("bundle", type=Path)
    impact_selection = impact_parser.add_mutually_exclusive_group(required=True)
    impact_selection.add_argument("--scenario")
    impact_selection.add_argument("--path", dest="source_path")
    impact_parser.add_argument("--direction", choices=["both", "upstream", "downstream"], default="both")
    impact_parser.add_argument("--max-depth", type=int, default=3)
    explain_parser = sub.add_parser("explain", help="explain a question with static evidence and on-demand historical traces")
    explain_parser.add_argument("bundle", type=Path, nargs="?", help="existing Bundle; omit when using --source-dir")
    explain_parser.add_argument("--source-dir", type=Path, help="inspect this Skill source automatically before explaining")
    explain_parser.add_argument("--case-id", help="case identifier for automatic source inspection")
    explain_parser.add_argument("--revision", help="source revision; defaults to clean Git commit or tree hash")
    explain_parser.add_argument("--cache-dir", type=Path)
    explain_parser.add_argument("--timeout-seconds", type=float, default=60.0)
    explain_selection = explain_parser.add_mutually_exclusive_group(required=True)
    explain_selection.add_argument("--capability", help="legacy object lookup")
    explain_selection.add_argument("--question", help="question-driven explanation")
    explain_parser.add_argument("--output", type=Path, help="report directory; required with --question")
    explain_parser.add_argument("--translation-file", type=Path,
                                help="graph-bound zh-CN translation sidecar for bilingual reports")
    explain_parser.add_argument("--static-only", action="store_true")
    explain_parser.add_argument("--trace-root", action="append", type=Path)
    explain_parser.add_argument("--session", help="restrict historical lookup to an existing session ID")
    explain_parser.add_argument("--max-traces", type=int, default=5)
    explain_parser.add_argument("--time-window", type=int, help="historical window in days")
    explain_parser.add_argument("--max-depth", type=int, default=4)
    explain_parser.add_argument("--max-nodes", type=int, default=120)
    explain_parser.add_argument("--max-chars", type=int, default=24000)
    apply_parser = sub.add_parser("apply-projection", help="validate and atomically apply an external Semantic Projection")
    apply_parser.add_argument("bundle", type=Path)
    apply_parser.add_argument("projection", type=Path)
    args = parser.parse_args()
    if args.command == "providers":
        manifest = provider_manifest()
        print(provider_table(manifest) if args.format == "table" else json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    if args.command == "summary":
        result = bundle_summary(args.bundle)
        print(summary_table(result) if args.format == "table" else json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == 'visualize':
        try:
            print(json.dumps(write_visual_report(args.bundle, args.output, args.instruction_analysis), ensure_ascii=False))
        except (OSError, ValueError) as error:
            raise SystemExit(f'cannot render visualization: {error}') from error
        return
    if args.command == 'mechanism':
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(build_mechanism_prompt(args.bundle, args.question, args.max_spans, args.max_chars), encoding='utf-8')
            print(json.dumps({'output': str(args.output), 'format': 'bounded-instruction-analysis-prompt'}, ensure_ascii=False))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f'cannot build mechanism prompt: {error}') from error
        return
    if args.command == 'apply-mechanism':
        try:
            print(json.dumps(apply_instruction_analysis(args.bundle, args.analysis), ensure_ascii=False))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f'cannot apply mechanism analysis: {error}') from error
        return
    if args.command == 'review-mechanism':
        try:
            result = review_instruction_analysis(args.bundle, args.analysis)
            rendered = json.dumps(result, ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + '\n', encoding='utf-8')
            print(rendered)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f'cannot review mechanism analysis: {error}') from error
        return
    if args.command == "diagnose":
        print(json.dumps(diagnose(args.bundle), ensure_ascii=False, indent=2))
        return
    if args.command == "ask":
        try:
            print(json.dumps(ask(args.bundle, args.question, args.limit, args.instruction_analysis), ensure_ascii=False, indent=2))
        except (OSError, ValueError) as error:
            raise SystemExit(f'cannot read instruction analysis: {error}') from error
        return
    if args.command == "trace":
        print(json.dumps(trace(args.bundle, args.scenario, args.direction, args.max_depth, args.source_path), ensure_ascii=False, indent=2))
        return
    if args.command == "impact":
        print(json.dumps(impact(args.bundle, args.scenario, args.direction, args.max_depth, args.source_path), ensure_ascii=False, indent=2))
        return
    if args.command == "explain":
        if args.source_dir:
            if args.capability:
                parser.error('--source-dir cannot be combined with --capability')
            if not args.question or not args.output:
                parser.error('--source-dir requires --question and --output')
            if args.bundle:
                parser.error('provide either bundle or --source-dir, not both')
            case_id = args.case_id or args.source_dir.name or 'skill'
            revision = args.revision
            if not revision:
                revision, revision_kind = source_revision(args.source_dir)
            else:
                revision_kind = 'provided'
            temporary_bundle = args.output / '.bundle'
            if temporary_bundle.exists():
                raise SystemExit('temporary Bundle already exists; choose a new output directory')
            try:
                manifest = inspect_source(args.source_dir, case_id, revision, temporary_bundle,
                                          args.timeout_seconds, args.cache_dir)
                manifest['revisionKind'] = revision_kind
                manifest_path = temporary_bundle / 'analysis-manifest.json'
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                result = build_explanation(temporary_bundle, args.question, args.static_only,
                                           args.trace_root, args.session, args.max_depth,
                                           args.max_nodes, args.max_chars, args.max_traces,
                                           args.time_window)
                report = write_explanation(result, temporary_bundle, args.output, args.translation_file)
                shutil.rmtree(temporary_bundle)
                report.update({'caseId': case_id, 'revision': revision, 'revisionKind': revision_kind,
                               'inspectedSource': str(args.source_dir.resolve()), 'status': manifest['status']})
                print(json.dumps(report, ensure_ascii=False))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise SystemExit(f'cannot explain source: {error}') from error
            return
        if not args.bundle:
            parser.error('explain requires a Bundle or --source-dir')
        if args.capability:
            print(json.dumps(explain(args.bundle, args.capability), ensure_ascii=False, indent=2))
            return
        if not args.output: parser.error('--question requires --output (report directory)')
        try:
            result = build_explanation(args.bundle, args.question, args.static_only, args.trace_root, args.session,
                                       args.max_depth, args.max_nodes, args.max_chars, args.max_traces, args.time_window)
            print(json.dumps(write_explanation(result, args.bundle, args.output, args.translation_file), ensure_ascii=False))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f'cannot explain: {error}') from error
        return
    if args.command == "apply-projection":
        try:
            print(json.dumps(apply_external_projection(args.bundle, args.projection), ensure_ascii=False))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"cannot apply projection: {error}") from error
        return
    if args.cache_dir and args.cache_dir.resolve().is_relative_to(args.source_dir.resolve()):
        raise SystemExit("cache directory must be outside the source tree")
    detected_providers = detect_providers(args.source_dir) if args.auto_providers else []
    planned_providers = plan_providers(
        {language for name in detected_providers for language in get_provider(name)["languages"]},
        args.facets,
    ) if args.auto_providers and args.facets else detected_providers
    unsupported = unsupported_extensions(args.source_dir) if args.auto_providers else []
    selection_mode = "explicit" if args.provider else ("auto" if args.auto_providers else "explicit")
    requested_providers = list(dict.fromkeys(args.provider or planned_providers or ( ["stdlib-baseline"] if args.facets else [])))
    run_names = list(dict.fromkeys(["stdlib-baseline", *requested_providers]))
    patches = []
    failures = []
    cache_records = []
    started = time.perf_counter()
    for name in run_names:
        try:
            if args.cache_dir:
                patch, cache_record = run_provider_cached(name, args, args.cache_dir)
                cache_records.append(cache_record)
                patches.append(patch)
            else:
                patches.append(run_provider(name, args))
        except (Exception, SystemExit) as error:
            if name == "stdlib-baseline" or args.fallback_provider == "none":
                raise
            failures.append({"requestedProvider": name, "reason": str(error)})
    graph = merge(patches) if len(patches) > 1 else patches[0]
    for failure in failures:
        record_provider_failure(graph, failure["requestedProvider"], failure["reason"])
    record_language_coverage_gap(graph, unsupported)
    facet_status = facet_coverage(requested_providers, args.facets)
    record_facet_coverage_gap(graph, {facet for facet, item in facet_status.items() if item["status"] == "unavailable"})
    with tempfile.NamedTemporaryFile(suffix=".graph-patch.json") as merged:
        merged.write(json.dumps(graph).encode("utf-8"))
        merged.flush()
        validate_patch(Path(merged.name))
    provider = graph["provider"]
    requested_provider = requested_providers[0] if len(requested_providers) == 1 else requested_providers
    fallback_reason = "; ".join(failure["reason"] for failure in failures) or None
    elapsed = round(time.perf_counter() - started, 6)
    projection = project(graph)
    artifact = {"schema": "skill-lens.artifact.v0.1", "caseId": args.case_id, "revision": args.revision, "path": str(args.source_dir), "kind": "unknown"}
    diagnostics = {"schema": "skill-lens.diagnostics.v0.1", "caseId": args.case_id, "revision": args.revision, "provider": provider, "requestedProvider": requested_provider, "requestedProviders": requested_providers, "requestedFacets": args.facets, "facetCoverage": facet_status, "selectionMode": selection_mode, "detectedProviders": detected_providers, "plannedProviders": planned_providers, "unsupportedExtensions": unsupported, "diagnostics": graph.get("diagnostics", [])}
    source_lock = load(args.sources_lock) if args.sources_lock else {"caseId": args.case_id, "revision": args.revision, "path": str(args.source_dir)}
    manifest = {"schema": "skill-lens.analysis-manifest.v0.1", "caseId": args.case_id, "revision": args.revision, "provider": provider, "requestedProvider": requested_provider, "requestedProviders": requested_providers, "requestedFacets": args.facets, "facetCoverage": facet_status, "selectionMode": selection_mode, "detectedProviders": detected_providers, "plannedProviders": planned_providers, "unsupportedExtensions": unsupported, "completedProviders": [patch["provider"] for patch in patches], "runtimeSeconds": elapsed, "timeoutSeconds": args.timeout_seconds, "sourceDir": str(args.source_dir), "projection": "rule-based-v0.1", "status": "partial" if graph.get("diagnostics") or fallback_reason else "complete", "fallbackReason": fallback_reason, "cache": {"enabled": bool(args.cache_dir), "directory": str(args.cache_dir) if args.cache_dir else None, "records": cache_records}}
    values = {"artifact.json": artifact, "evidence-graph.json": graph, "semantic-projection.json": projection, "diagnostics.json": diagnostics, "sources.lock.json": source_lock, "analysis-manifest.json": manifest}
    write_bundle(args.out, values)
    print(json.dumps({"bundle": str(args.out), "caseId": args.case_id, "provider": provider, "nodeCount": len(graph["nodes"]), "edgeCount": len(graph["edges"]), "diagnosticCount": len(graph["diagnostics"]), "status": manifest["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
