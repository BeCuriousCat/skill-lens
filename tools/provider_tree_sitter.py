#!/usr/bin/env python3
"""Tree-sitter syntax provider for the M4 bake-off.

This provider reports syntax facts only. It deliberately does not infer symbol
resolution, cross-file calls, runtime dispatch, or framework semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from config_scalar_fields import fields as config_fields

try:
    from tree_sitter_language_pack import get_parser
except ImportError as exc:  # pragma: no cover - exercised by capability probe
    raise SystemExit("tree-sitter-language-pack is required; use the pinned bake-off environment") from exc


PROVIDER = {"id": "tree-sitter-syntax", "version": "0.3.0"}
SUFFIXES = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".sh": "bash", ".bash": "bash", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".markdown": "markdown", ".html": "html", ".css": "css", ".toml": "toml",
}
DEFINITION_TYPES = {"function_definition", "function_declaration", "method_definition", "class_definition", "class_declaration"}
JS_FUNCTIONS = {"function_declaration", "method_definition", "arrow_function", "function_expression", "generator_function", "generator_function_declaration"}
CALL_TYPES = {"call", "call_expression", "new_expression"}
IMPORT_TYPES = {"import_statement", "import_declaration", "import_clause", "import_specifier", "use_declaration"}


def stable(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def span(node) -> str:
    a = node.start_point
    b = node.end_point
    return f"{a[0] + 1}:{a[1]}-{b[0] + 1}:{b[1]}"


def line_range(node) -> str:
    a = node.start_point[0] + 1
    b = node.end_point[0] + 1
    return str(a) if a == b else f"{a}-{b}"


def text_for(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip().replace("\n", " ")[:180]


def descendants(node):
    """Yield a deterministic depth-first traversal of a Tree-sitter node."""
    yield node
    for child in node.children:
        yield from descendants(child)


def evidence(revision: str, path: str, lines: str, quote: str) -> dict:
    return {
        "id": stable("evidence", f"{revision}:{path}:{lines}:{quote}"),
        "sourceType": "code",
        "source": {"revision": revision, "path": path, "lines": lines},
        "quote": quote[:500] or "syntax node",
        "confidence": "medium",
    }


def scan(root: Path, revision: str, case_id: str) -> dict:
    nodes, edges, refs, diagnostics = [], [], [], []
    file_components = {}
    file_definitions = {}
    pending_imports = []
    files = sorted(path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)
    supported = [path for path in files if path.suffix.lower() in SUFFIXES]
    artifact_id = stable("node", f"artifact:{case_id}:{revision}")
    tree_ref = evidence(revision, ".", "tree", f"Tree-sitter provider found {len(supported)} supported syntax files")
    refs.append(tree_ref)
    nodes.append({"id": artifact_id, "type": "Artifact", "label": case_id, "attributes": {"supportedSyntaxFileCount": len(supported)}, "evidence": [tree_ref["id"]]})
    if len(supported) < len(files):
        diagnostics.append({"code": "unsupported-file-types", "severity": "info", "message": "files with unrecognized suffixes were outside the syntax provider scope", "extensions": {"totalFiles": len(files), "supportedFiles": len(supported)}})

    for path in supported:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        language = SUFFIXES[path.suffix.lower()]
        component_id = stable("node", f"component:{case_id}:{relative}:{revision}")
        file_ref = evidence(revision, relative, f"1-{max(1, content.count(bytes(chr(10), 'utf-8')) + 1)}", f"syntax file {relative} exists")
        refs.append(file_ref)
        nodes.append({"id": component_id, "type": "Component", "label": relative, "attributes": {"suffix": path.suffix.lower(), "language": language, "file": True}, "evidence": [file_ref["id"]]})
        file_components[relative] = component_id
        edges.append({"id": stable("edge", f"{artifact_id}:contains:{component_id}"), "from": artifact_id, "to": component_id, "type": "contains"})
        try:
            tree = get_parser(language).parse(content)
        except Exception as error:
            diagnostics.append({"code": "language-parser-error", "severity": "warning", "message": str(error), "path": relative})
            continue
        if tree.root_node.has_error:
            diagnostics.append({"code": "syntax-errors", "severity": "warning", "message": "tree-sitter reported syntax errors", "path": relative})
        if language in {'json', 'yaml', 'toml'}:
            records, gaps = config_fields(tree.root_node, content, language)
            for record in records:
                source_lines = f"{record['startLine']}-{record['endLine']}"
                key_path = '.'.join(record['keyPath'])
                ref = evidence(revision, relative, source_lines, f"config field {key_path} = {record['rawValue']}")
                refs.append(ref)
                fact_id = stable('node', f"config-field:{case_id}:{relative}:{record['startByte']}:{revision}")
                attrs = {key: value for key, value in record.items() if key not in {'startLine', 'endLine', 'startByte'}}
                attrs.update({'kind': 'config-field', 'language': language, 'staticOnly': True,
                              'evidenceExcerptTruncated': len(f"config field {key_path} = {record['rawValue']}") > 500})
                nodes.append({'id': fact_id, 'type': 'Component', 'label': f'{relative}:{key_path}',
                              'attributes': attrs, 'evidence': [ref['id']]})
                edges.append({'id': stable('edge', f'{component_id}:declares:{fact_id}'),
                              'from': component_id, 'to': fact_id, 'type': 'declares', 'evidence': [ref['id']]})
            for gap in gaps:
                diagnostics.append({'code': 'config-field-' + gap, 'severity': 'info',
                    'message': 'Config scalar extraction boundary: ' + gap + '; application loading is not established', 'path': relative})
        js = language in {"javascript", "typescript", "tsx"}
        definitions = DEFINITION_TYPES | JS_FUNCTIONS if js else DEFINITION_TYPES
        selected = [item for item in descendants(tree.root_node) if item.type in definitions | CALL_TYPES | IMPORT_TYPES]
        ids = {item: stable("node", f"{case_id}:{relative}:{item.type}:{span(item)}:{text_for(item, content)}:{revision}") for item in selected}

        def body_owner(item):
            parent = item.parent
            while parent is not None:
                if js and parent.type in JS_FUNCTIONS:
                    body = parent.child_by_field_name("body")
                    if body and body.start_byte <= item.start_byte and item.end_byte <= body.end_byte:
                        return ids[parent]
                parent = parent.parent
            return component_id

        for item in selected:
            node_type = item.type
            item_text = text_for(item, content)
            raw_text = content[item.start_byte:item.end_byte].decode("utf-8", errors="replace")
            ref = evidence(revision, relative, span(item), f"{node_type}: {item_text}")
            refs.append(ref)
            fact_type = "Capability" if node_type in definitions else "Scenario" if node_type in CALL_TYPES else "Component"
            fact_id = ids[item]
            attrs = {"syntaxType": node_type, "language": language, "span": span(item)}
            name = item.child_by_field_name("name")
            if js and node_type in JS_FUNCTIONS and item.parent and item.parent.type == "variable_declarator":
                name = item.parent.child_by_field_name("name")
            if name is not None:
                attrs["name"] = text_for(name, content)
                if node_type in definitions:
                    file_definitions.setdefault(relative, {}).setdefault(attrs["name"], []).append(fact_id)
            nodes.append({"id": fact_id, "type": fact_type, "label": f"{attrs.get('name', node_type)} in {relative}", "attributes": attrs, "evidence": [ref["id"]]})
            relation = "declares" if node_type in definitions else "invokes" if node_type in CALL_TYPES else "imports"
            owner = body_owner(item)
            edges.append({"id": stable("edge", f"{owner}:{relation}:{fact_id}"), "from": owner, "to": fact_id, "type": relation})
            if node_type in IMPORT_TYPES and js:
                match = re.search(r"['\"](\.?\.?/[^'\"]+)['\"]", raw_text)
                if match:
                    pending_imports.append((fact_id, relative, match.group(1), ref["id"], raw_text))
            if js and node_type in JS_FUNCTIONS and item.parent and item.parent.type == "arguments":
                call = item.parent.parent
                if call in ids and call.type in CALL_TYPES:
                    caller = ids[call]
                    edges.append({"id": stable("edge", f"{caller}:passes-callback:{fact_id}"), "from": caller, "to": fact_id, "type": "passes-callback", "evidence": [ref["id"]]})
    for import_id, importer, specifier, evidence_id, raw_import in pending_imports:
        base = Path(importer).parent / specifier
        candidates = [base.as_posix()]
        if base.suffix in {".js", ".jsx"}:
            candidates.extend((base.with_suffix(suffix)).as_posix() for suffix in (".ts", ".tsx", ".js", ".jsx"))
        if not base.suffix:
            candidates.extend((base.as_posix() + suffix) for suffix in (".ts", ".tsx", ".js", ".jsx"))
            candidates.extend((base / name).as_posix() for name in ("index.ts", "index.tsx", "index.js", "index.jsx"))
        target = next((file_components.get(candidate) for candidate in candidates if candidate in file_components), None)
        if target:
            edges.append({"id": stable("edge", f"{import_id}:resolves-to:{target}"), "from": import_id, "to": target, "type": "resolves-to", "evidence": [evidence_id]})
            target_path = next(candidate for candidate in candidates if candidate in file_components)
            clause = raw_import.split(" from ", 1)[0]
            imported_names = re.findall(r"\b[A-Za-z_$][\w$]*\b", clause.replace("import", "", 1))
            imported_names = [name for name in imported_names if name not in {"type", "as"}]
            for name in imported_names:
                for definition_id in file_definitions.get(target_path, {}).get(name, []):
                    edges.append({"id": stable("edge", f"{import_id}:resolves-symbol:{definition_id}"), "from": import_id, "to": definition_id, "type": "resolves-symbol", "evidence": [evidence_id]})
        else:
            diagnostics.append({"code": "local-import-unresolved", "severity": "info", "message": "relative import target was not found in the scanned file set", "path": importer, "extensions": {"specifier": specifier}})
    diagnostics.append({"code": "tree-sitter-syntax-boundary", "severity": "info", "message": "syntax facts only; symbol resolution, semantic calls, runtime dispatch, and cross-file relations are unresolved"})
    if any(path.suffix.lower() in {'.json', '.yaml', '.yml', '.toml'} for path in supported):
        diagnostics.append({'code': 'config-scalar-boundary', 'severity': 'info',
            'message': 'Config fields preserve scalar source syntax and key paths only; arrays, anchors/tags, application schemas and host loading are not resolved. Sensitive paths are omitted and duplicate paths flagged.'})
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
    print(f"{args.case_id}: supported_files={result['nodes'][0]['attributes']['supportedSyntaxFileCount']} nodes={len(result['nodes'])} edges={len(result['edges'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
