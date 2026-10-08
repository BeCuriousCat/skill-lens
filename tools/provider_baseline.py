#!/usr/bin/env python3
"""Conservative dependency-free baseline provider for locked benchmark sources."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from enricher_hook_static import enrich


def stable(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def evidence(revision: str, path: str, lines: str, quote: str) -> dict:
    return {
        "id": stable("evidence", f"{revision}:{path}:{lines}:{quote}"),
        "sourceType": "code" if Path(path).suffix in {".py", ".js", ".ts", ".sh", ".json", ".yaml", ".yml", ".toml"} else "doc",
        "source": {"revision": revision, "path": path, "lines": lines},
        "quote": quote[:500],
        "confidence": "medium"
    }


def scan(root: Path, revision: str, case_id: str) -> dict:
    nodes, edges, refs, diagnostics = [], [], [], []
    contents = {}
    files = sorted(path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)
    if not files:
        diagnostics.append({"code": "no-files", "severity": "error", "message": "source directory is empty"})
    artifact_id = stable("node", f"artifact:{case_id}:{revision}")
    tree_ref = evidence(revision, ".", "tree", f"temporary checkout contains {len(files)} files")
    refs.append(tree_ref)
    nodes.append({"id": artifact_id, "type": "Artifact", "label": case_id, "attributes": {"fileCount": len(files)}, "evidence": [tree_ref["id"]]})

    for path in files:
        relative = path.relative_to(root).as_posix()
        # Preserve line-ending bytes: normalizing CRLF can make invalid Shell
        # heredoc delimiters look like the supported literal script subset.
        content = path.read_bytes().decode("utf-8", errors="replace")
        contents[relative] = content
        lines = content.split("\n") if content else []
        if content.endswith("\n"):
            lines.pop()
        component_id = stable("node", f"component:{case_id}:{relative}:{revision}")
        component_ref = evidence(revision, relative, f"1-{max(1, len(lines))}", f"file {relative} exists in the locked source tree")
        refs.append(component_ref)
        nodes.append({"id": component_id, "type": "Component", "label": relative, "attributes": {"suffix": path.suffix, "lineCount": len(lines)}, "evidence": [component_ref["id"]]})
        edges.append({"id": stable("edge", f"{artifact_id}:contains:{component_id}"), "from": artifact_id, "to": component_id, "type": "contains"})
        if path.name == "SKILL.md":
            heading_lines = [str(index + 1) for index, line in enumerate(lines) if line.startswith("#")]
            if heading_lines:
                ref = evidence(revision, relative, ",".join(heading_lines[:12]), "Markdown headings define instruction sections")
                refs.append(ref)
                node_id = stable("node", f"headings:{relative}:{revision}")
                nodes.append({"id": node_id, "type": "Capability", "label": f"instruction sections in {relative}", "attributes": {"headingCount": len(heading_lines)}, "evidence": [ref["id"]]})
                edges.append({"id": stable("edge", f"{component_id}:declares:{node_id}"), "from": component_id, "to": node_id, "type": "declares"})
            if lines and lines[0].strip() == "---":
                closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
                if closing is None:
                    diagnostics.append({"code": "frontmatter-unclosed", "severity": "warning", "path": relative})
                else:
                    ref = evidence(revision, relative, f"1-{closing + 1}", "YAML frontmatter detected")
                    refs.append(ref)
                    node_id = stable("node", f"frontmatter:{relative}:{revision}")
                    nodes.append({"id": node_id, "type": "Component", "label": f"frontmatter in {relative}", "attributes": {"lines": closing + 1}, "evidence": [ref["id"]]})
                    edges.append({"id": stable("edge", f"{component_id}:contains:{node_id}"), "from": component_id, "to": node_id, "type": "contains"})
    graph = {"schema": "skill-lens.graph-patch.v0.1", "provider": {"id": "stdlib-baseline", "version": "0.1.3"}, "revision": revision, "caseId": case_id, "nodes": nodes, "edges": edges, "evidence": refs, "diagnostics": diagnostics}
    enrich(graph, root, contents, stable, evidence)
    if not any(node["type"] in {"Capability", "Scenario"} for node in nodes):
        diagnostics.append({"code": "no-entrypoint-like-facts", "severity": "warning", "message": "baseline does not infer an entrypoint"})
    diagnostics.append({"code": "limited-parser", "severity": "info", "message": "baseline uses standard-library text scans; semantic calls and dynamic dispatch are unresolved"})
    return graph


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
    print(f"{args.case_id}: files={len([n for n in result['nodes'] if n['type'] == 'Component'])} nodes={len(result['nodes'])} edges={len(result['edges'])} diagnostics={len(result['diagnostics'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
