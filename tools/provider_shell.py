#!/usr/bin/env python3
"""Dependency-free, conservative Shell provider.

It extracts source-visible function definitions, loops, and simple command
candidates. It does not claim complete command coverage, command resolution,
shell execution, exit status, expanded variables, or remote responses.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


PROVIDER = {"id": "shell-syntax", "version": "0.1.0"}
FUNCTION = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*\{")
COMMAND = re.compile(r"^\s*(?:command\s+)?([A-Za-z_./:-][A-Za-z0-9_./:-]*)\b")
CONTROL = {"if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case", "esac", "in", "function"}
ASSIGNMENT = re.compile(r"^(?:(?:local|export|readonly)\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\[[^]]+\])?=")


def stable(prefix, value):
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def evidence(revision, path, line, quote):
    return {"id": stable("evidence", f"{revision}:{path}:{line}:{quote}"), "sourceType": "code", "source": {"revision": revision, "path": path, "lines": str(line)}, "quote": quote[:500], "confidence": "medium"}


def scan(root: Path, revision: str, case_id: str) -> dict:
    nodes, edges, refs, diagnostics = [], [], [], []
    files = sorted(p for p in root.rglob("*.sh") if p.is_file() and ".git" not in p.parts)
    artifact = stable("node", f"artifact:{case_id}:{revision}")
    ref = evidence(revision, ".", "tree", f"Shell provider found {len(files)} shell files")
    refs.append(ref)
    nodes.append({"id": artifact, "type": "Artifact", "label": case_id, "attributes": {"shellFileCount": len(files)}, "evidence": [ref["id"]]})
    if not files:
        diagnostics.append({"code": "no-shell-files", "severity": "warning", "message": "no Shell files were found"})
    for path in files:
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        component = stable("node", f"component:{case_id}:{relative}:{revision}")
        ref = evidence(revision, relative, f"1-{max(1, len(lines))}", f"Shell file {relative} exists in the locked source tree")
        refs.append(ref)
        nodes.append({"id": component, "type": "Component", "label": relative, "attributes": {"suffix": ".sh", "lineCount": len(lines)}, "evidence": [ref["id"]]})
        edges.append({"id": stable("edge", f"{artifact}:contains:{component}"), "from": artifact, "to": component, "type": "contains"})
        in_single_quote = False
        for number, line in enumerate(lines, 1):
            if in_single_quote:
                if line.count("'") % 2:
                    in_single_quote = False
                continue
            match = FUNCTION.match(line)
            if match:
                name = match.group(1)
                ref = evidence(revision, relative, number, f"shell function definition {name}")
                refs.append(ref)
                node = stable("node", f"function:{case_id}:{relative}:{name}:{number}:{revision}")
                nodes.append({"id": node, "type": "Component", "label": f"{relative}:{name}", "attributes": {"kind": "shell-function"}, "evidence": [ref["id"]]})
                edges.append({"id": stable("edge", f"{component}:declares:{node}"), "from": component, "to": node, "type": "declares"})
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ASSIGNMENT.match(stripped) or stripped.startswith(("[", "(", "${", '"', "'")) or stripped.endswith(")"):
                continue
            if stripped == "}":
                continue
            if re.match(r"^(for|while)\b", stripped):
                ref = evidence(revision, relative, number, f"shell loop construct: {stripped}")
                refs.append(ref)
                node = stable("node", f"loop:{case_id}:{relative}:{number}:{revision}")
                nodes.append({"id": node, "type": "Scenario", "label": f"loop in {relative}:{number}", "attributes": {"kind": "loop", "construct": stripped.split()[0]}, "evidence": [ref["id"]]})
                edges.append({"id": stable("edge", f"{component}:contains:{node}"), "from": component, "to": node, "type": "contains"})
                continue
            match = COMMAND.match(stripped)
            if not match or match.group(1) in CONTROL or stripped.endswith("{"):
                continue
            command = match.group(1)
            if command in {"curl", "node", "sed", "awk", "jq", "python", "python3"} and line.count("'") % 2:
                in_single_quote = True
            if command in {"echo", "printf", "export", "local", "return", "exit", "set", "shift", "source", "."}:
                continue
            ref = evidence(revision, relative, number, f"shell command candidate {command}")
            refs.append(ref)
            node = stable("node", f"command:{case_id}:{relative}:{number}:{command}:{revision}")
            nodes.append({"id": node, "type": "Scenario", "label": f"command candidate {command} in {relative}:{number}", "attributes": {"kind": "command-candidate", "command": command}, "evidence": [ref["id"]]})
            edges.append({"id": stable("edge", f"{component}:invokes:{node}"), "from": component, "to": node, "type": "invokes"})
    diagnostics.append({"code": "shell-static-boundary", "severity": "warning", "message": "simple line-leading Shell command candidates only; assignments, command substitutions, conditions, pipelines, functions versus external commands, multiline quoting, expansion, execution status, and remote responses are unresolved"})
    return {"schema": "skill-lens.graph-patch.v0.1", "provider": PROVIDER, "revision": revision, "caseId": case_id, "nodes": nodes, "edges": edges, "evidence": refs, "diagnostics": diagnostics}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = scan(args.source_dir, args.revision, args.case_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.case_id}: shell_files={result['nodes'][0]['attributes']['shellFileCount']} nodes={len(result['nodes'])} edges={len(result['edges'])}")


if __name__ == "__main__":
    main()
