#!/usr/bin/env python3
"""Experimental PowerShell syntax Provider.

The Provider emits source-visible syntax facts from the pinned Tree-sitter
PowerShell grammar. It deliberately does not resolve commands or modules and
keeps parser errors as diagnostics instead of treating a partial parse as a
complete program model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from tree_sitter_language_pack import get_parser
except ImportError as exc:  # pragma: no cover - exercised by dependency fallback
    raise SystemExit("tree-sitter-language-pack is required; use the pinned PowerShell runtime") from exc


PROVIDER = {"id": "powershell-syntax", "version": "0.1.0"}
SUFFIXES = {".ps1": "powershell"}
FACET_TYPES = {
    "function_statement": ("Capability", "function-definitions", "declares"),
    "if_statement": ("Scenario", "conditionals", "contains"),
    "foreach_statement": ("Scenario", "loops", "contains"),
    "for_statement": ("Scenario", "loops", "contains"),
    "while_statement": ("Scenario", "loops", "contains"),
    "command": ("Scenario", "command-candidates", "invokes"),
}


def stable(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def span(node) -> str:
    start, end = node.start_point, node.end_point
    return f"{start[0] + 1}:{start[1]}-{end[0] + 1}:{end[1]}"


def line_range(node) -> str:
    start, end = node.start_point[0] + 1, node.end_point[0] + 1
    return str(start) if start == end else f"{start}-{end}"


def text_for(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip().replace("\n", " ")[:180]


def descendants(node):
    yield node
    for child in node.children:
        yield from descendants(child)


def evidence(revision: str, path: str, lines: str, quote: str) -> dict:
    return {
        "id": stable("evidence", f"{revision}:{path}:{lines}:{quote}"),
        "sourceType": "code",
        "source": {"revision": revision, "path": path, "lines": lines},
        "quote": quote[:500] or "PowerShell syntax node",
        "confidence": "medium",
    }


def scan(root: Path, revision: str, case_id: str) -> dict:
    nodes, edges, refs, diagnostics = [], [], [], []
    files = sorted(path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)
    supported = [path for path in files if path.suffix.lower() in SUFFIXES]
    artifact_id = stable("node", f"artifact:{case_id}:{revision}")
    tree_ref = evidence(revision, ".", "tree", f"PowerShell Provider found {len(supported)} supported files")
    refs.append(tree_ref)
    nodes.append({"id": artifact_id, "type": "Artifact", "label": case_id, "attributes": {"supportedPowerShellFileCount": len(supported)}, "evidence": [tree_ref["id"]]})
    if len(supported) < len(files):
        diagnostics.append({"code": "unsupported-file-types", "severity": "info", "message": "files with unrecognized suffixes were outside the PowerShell Provider scope", "extensions": {"totalFiles": len(files), "supportedFiles": len(supported)}})
    for path in supported:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        component_id = stable("node", f"component:{case_id}:{relative}:{revision}")
        file_ref = evidence(revision, relative, f"1-{max(1, content.count(bytes([10])) + 1)}", f"PowerShell file {relative} exists")
        refs.append(file_ref)
        nodes.append({"id": component_id, "type": "Component", "label": relative, "attributes": {"suffix": path.suffix.lower(), "language": "powershell", "file": True}, "evidence": [file_ref["id"]]})
        edges.append({"id": stable("edge", f"{artifact_id}:contains:{component_id}"), "from": artifact_id, "to": component_id, "type": "contains"})
        try:
            tree = get_parser("powershell").parse(content)
        except Exception as error:
            diagnostics.append({"code": "language-parser-error", "severity": "warning", "message": str(error), "path": relative})
            continue
        if tree.root_node.has_error:
            diagnostics.append({"code": "syntax-errors", "severity": "warning", "message": "tree-sitter reported syntax errors; emitted facts are partial", "path": relative})
        selected = [item for item in descendants(tree.root_node) if item.type in FACET_TYPES]
        ids = {item: stable("node", f"{case_id}:{relative}:{item.type}:{span(item)}:{text_for(item, content)}:{revision}") for item in selected}
        for item in selected:
            kind, facet, relation = FACET_TYPES[item.type]
            raw = text_for(item, content)
            name_node = item.child_by_field_name("name")
            if name_node is None and item.type == "function_statement":
                name_node = next((child for child in descendants(item) if child.type == "function_name"), None)
            name = text_for(name_node, content) if name_node is not None else ""
            if item.type == "command":
                command_name = item.child_by_field_name("name")
                if command_name is None:
                    command_name = next((child for child in descendants(item) if child.type == "command_name"), None)
                name = text_for(command_name, content) if command_name is not None else "unknown"
            ref = evidence(revision, relative, line_range(item), f"PowerShell {facet} {name}: {raw}")
            refs.append(ref)
            attrs = {"kind": facet, "syntaxType": item.type, "language": "powershell", "span": span(item)}
            if name:
                attrs["name"] = name
            node_id = ids[item]
            nodes.append({"id": node_id, "type": kind, "label": f"{relative}:{name or item.type}", "attributes": attrs, "evidence": [ref["id"]]})
            owner = component_id
            parent = item.parent
            while parent is not None:
                if parent in ids and FACET_TYPES[parent.type][0] in {"Capability", "Scenario"}:
                    owner = ids[parent]
                    break
                parent = parent.parent
            edges.append({"id": stable("edge", f"{owner}:{relation}:{node_id}"), "from": owner, "to": node_id, "type": relation, "evidence": [ref["id"]]})
    diagnostics.append({"code": "powershell-static-boundary", "severity": "warning", "message": "PowerShell syntax facts only; command resolution, module/import resolution, runtime dispatch, environment effects, execution outcomes, and complete grammar coverage are unresolved"})
    return {"schema": "skill-lens.graph-patch.v0.1", "provider": PROVIDER, "revision": revision, "caseId": case_id, "nodes": nodes, "edges": edges, "evidence": refs, "diagnostics": diagnostics}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = scan(args.source_dir, args.revision, args.case_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.case_id}: powershell_files={result['nodes'][0]['attributes']['supportedPowerShellFileCount']} nodes={len(result['nodes'])} edges={len(result['edges'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
