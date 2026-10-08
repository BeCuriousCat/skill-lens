#!/usr/bin/env python3
"""Dependency-free Python AST provider for the Skill Lens bake-off.

This adapter deliberately reports only facts recoverable from Python's built-in
AST: files, function definitions, imports, bounded source statements with
enclosing lexical regions, syntactic call sites, and narrow
same-file and same-tree cross-file direct-call relations. It does not claim
runtime dispatch, alias or re-export resolution, or cross-language relations.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

from python_source_context import is_source_statement, nearest_source_statement, source_statement_fact


PROVIDER = {"id": "python-ast", "version": "0.4.1"}


def stable(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def make_evidence(revision: str, path: str, lines: str, quote: str) -> dict:
    return {
        "id": stable("evidence", f"{revision}:{path}:{lines}:{quote}"),
        "sourceType": "code",
        "source": {"revision": revision, "path": path, "lines": lines},
        "quote": quote[:500],
        "confidence": "medium",
    }


def line_range(node: ast.AST) -> str:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    return str(start) if start == end else f"{start}-{end}"


def source_position(node: ast.AST) -> str:
    start_line = getattr(node, "lineno", 1)
    start_col = getattr(node, "col_offset", 0)
    end_line = getattr(node, "end_lineno", start_line)
    end_col = getattr(node, "end_col_offset", start_col)
    return f"{start_line}:{start_col}-{end_line}:{end_col}"


def dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def parse_source_tree(root: Path, files: list[Path], diagnostics: list[dict]) -> tuple[
    dict[str, tuple[str, ast.Module]], dict[str, dict[str, list[ast.AST]]]
]:
    modules: dict[str, tuple[str, ast.Module]] = {}
    definitions: dict[str, dict[str, list[ast.AST]]] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=relative)
        except SyntaxError as error:
            diagnostics.append({"code": "python-parse-error", "severity": "warning", "message": str(error), "path": relative})
            continue
        module = module_name(root, path)
        modules[module] = (relative, tree)
        definitions[module] = {}
        for item in tree.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions[module].setdefault(item.name, []).append(item)
    return modules, definitions


def cross_file_imports(
    tree: ast.Module,
    modules: dict[str, tuple[str, ast.Module]],
    definitions: dict[str, dict[str, list[ast.AST]]],
) -> dict[str, tuple[str, str]]:
    """Resolve only unaliased, absolute from-imports to unique top-level symbols."""
    imported: dict[str, tuple[str, str]] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom) or statement.level or not statement.module:
            continue
        target_module = statement.module if statement.module in modules else f"{statement.module}.__init__"
        if target_module not in modules:
            continue
        for alias in statement.names:
            if alias.name == "*" or alias.asname:
                continue
            candidates = definitions.get(target_module, {}).get(alias.name, [])
            if len(candidates) == 1:
                imported[alias.name] = (target_module, alias.name)

    # A module-level assignment or duplicate definition makes the binding
    # ambiguous for every call in that module. Keep the route unresolved.
    rebound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            rebound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not tree:
            if node.name in imported and node in tree.body:
                rebound.add(node.name)
    return {name: target for name, target in imported.items() if name not in rebound}


def bound_names(node: ast.AST) -> set[str]:
    """Collect possible bindings, including nested scopes as a conservative veto."""
    names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del))}
    for item in ast.walk(node):
        if isinstance(item, ast.arg):
            names.add(item.arg)
        elif item is not node and isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(item.name)
        elif isinstance(item, (ast.Import, ast.ImportFrom)):
            for alias in item.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(item, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and item.name:
            names.add(item.name)
    return names


def module_bound_names(tree: ast.Module, excluded: ast.AST) -> set[str]:
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        if node is excluded:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            return
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in tree.body:
        visit(statement)
    return names


def direct_call_scope(call: ast.Call, parents: dict[ast.AST, ast.AST], tree: ast.Module) -> ast.AST | None:
    child: ast.AST = call
    parent = parents.get(child)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent if parent in tree.body and child in parent.body else None
        if isinstance(parent, (ast.Lambda, ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return None
        if parent is tree:
            return tree
        child, parent = parent, parents.get(parent)
    return None


def scan(root: Path, revision: str, case_id: str) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    evidence: list[dict] = []
    diagnostics: list[dict] = []
    files = sorted(path for path in root.rglob("*.py") if path.is_file() and ".git" not in path.parts)
    modules, definitions = parse_source_tree(root, files, diagnostics)
    pending_cross_file: list[tuple[str, str, str, str, str]] = []
    definition_ids: dict[tuple[str, str], str] = {}
    artifact_id = stable("node", f"artifact:{case_id}:{revision}")
    tree_ref = make_evidence(revision, ".", "tree", f"Python AST provider found {len(files)} Python files")
    evidence.append(tree_ref)
    nodes.append({"id": artifact_id, "type": "Artifact", "label": case_id, "attributes": {"pythonFileCount": len(files)}, "evidence": [tree_ref["id"]]})
    if not files:
        diagnostics.append({"code": "no-python-files", "severity": "warning", "message": "no Python files were found"})

    for path in files:
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        component_id = stable("node", f"component:{case_id}:{relative}:{revision}")
        file_ref = make_evidence(revision, relative, f"1-{max(1, len(text.splitlines()))}", f"Python file {relative} exists in the locked source tree")
        evidence.append(file_ref)
        nodes.append({"id": component_id, "type": "Component", "label": relative, "attributes": {"suffix": ".py"}, "evidence": [file_ref["id"]]})
        edges.append({"id": stable("edge", f"{artifact_id}:contains:{component_id}"), "from": artifact_id, "to": component_id, "type": "contains"})
        module = module_name(root, path)
        parsed = modules.get(module)
        if parsed is None:
            continue
        tree = parsed[1]
        symbols: dict[ast.AST, str] = {}
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        top_level_functions = {
            item.name: item for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        repeated_bindings = {
            name for name, definition in top_level_functions.items()
            if name in module_bound_names(tree, definition)
        }
        repeated_bindings.update(
            name for item in ast.walk(tree) if isinstance(item, ast.Global)
            for name in item.names
        )
        star_import = any(isinstance(item, ast.ImportFrom) and any(alias.name == "*" for alias in item.names) for item in ast.walk(tree))
        for item in ast.walk(tree):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(item, ast.ClassDef) else "function"
                description = f"{kind} definition {item.name}"
                ref = make_evidence(revision, relative, line_range(item), description)
                evidence.append(ref)
                symbol_id = stable("node", f"symbol:{case_id}:{relative}:{item.name}:{item.lineno}:{revision}")
                symbols[item] = symbol_id
                attributes = {"kind": kind}
                if kind == "function":
                    attributes["async"] = isinstance(item, ast.AsyncFunctionDef)
                nodes.append({"id": symbol_id, "type": "Component", "label": f"{relative}:{item.name}", "attributes": attributes, "evidence": [ref["id"]]})
                edges.append({"id": stable("edge", f"{component_id}:declares:{symbol_id}"), "from": component_id, "to": symbol_id, "type": "declares"})
                if item in tree.body:
                    definition_ids[(relative, item.name)] = symbol_id
        imported_symbols = cross_file_imports(tree, modules, definitions)
        statement_ids = {}
        for item in ast.walk(tree):
            if not is_source_statement(item):
                continue
            fact = source_statement_fact(item, text, parents)
            ref = make_evidence(revision, relative, f"{fact['startLine']}-{fact['endLine']}", fact['quote'])
            evidence.append(ref)
            statement_id = stable("node", f"source-statement:{case_id}:{relative}:{source_position(item)}:{revision}")
            statement_ids[item] = (statement_id, ref['id'])
            nodes.append({"id": statement_id, "type": "Scenario", "label": f"{relative}:{item.lineno} {type(item).__name__}",
                          "attributes": fact['attributes'], "evidence": [ref['id']]})
            edges.append({"id": stable("edge", f"{component_id}:contains:{statement_id}"),
                          "from": component_id, "to": statement_id, "type": "contains", "evidence": [ref['id']]})
        for item in ast.walk(tree):
            if isinstance(item, (ast.Import, ast.ImportFrom)):
                name = item.module if isinstance(item, ast.ImportFrom) else ",".join(alias.name for alias in item.names)
                ref = make_evidence(revision, relative, line_range(item), f"import statement {name}")
                evidence.append(ref)
                import_id = stable("node", f"import:{case_id}:{relative}:{source_position(item)}:{revision}")
                nodes.append({"id": import_id, "type": "Component", "label": f"import {name}", "attributes": {"kind": "import"}, "evidence": [ref["id"]]})
                edges.append({"id": stable("edge", f"{component_id}:imports:{import_id}"), "from": component_id, "to": import_id, "type": "imports"})
            if isinstance(item, ast.Call):
                target = dotted_name(item.func)
                if not target:
                    diagnostics.append({"code": "dynamic-call-unresolved", "severity": "info", "message": "call target is not a statically named function", "path": relative, "extensions": {"line": getattr(item, "lineno", 1)}})
                    continue
                parent = parents.get(item)
                while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parent = parents.get(parent)
                owner = symbols.get(parent, component_id)
                ref = make_evidence(revision, relative, line_range(item), f"syntactic call to {target} at {source_position(item)}")
                evidence.append(ref)
                call_id = stable("node", f"call:{case_id}:{relative}:{source_position(item)}:{target}:{revision}")
                nodes.append({"id": call_id, "type": "Scenario", "label": f"call {target}", "attributes": {"target": target, "kind": "call"}, "evidence": [ref["id"]]})
                edges.append({"id": stable("edge", f"{owner}:invokes:{call_id}"), "from": owner, "to": call_id, "type": "invokes"})
                statement = nearest_source_statement(item, parents)
                if statement in statement_ids:
                    statement_id, statement_ref = statement_ids[statement]
                    edges.append({"id": stable("edge", f"{statement_id}:contains:{call_id}"), "from": statement_id,
                                  "to": call_id, "type": "contains", "evidence": [statement_ref]})
                definition = top_level_functions.get(target)
                scope = direct_call_scope(item, parents, tree)
                if (isinstance(item.func, ast.Name) and definition is not None and not definition.decorator_list
                        and target not in repeated_bindings and not star_import and scope is not None):
                    if ((scope is tree and definition.lineno < item.lineno)
                            or (scope is not tree and target not in bound_names(scope))):
                        target_id = symbols[definition]
                        edges.append({"id": stable("edge", f"{call_id}:resolves-symbol:{target_id}"), "from": call_id, "to": target_id, "type": "resolves-symbol", "evidence": [ref["id"]]})
                cross_target = imported_symbols.get(target) if isinstance(item.func, ast.Name) else None
                if cross_target and scope is not None and (scope is tree or target not in bound_names(scope)):
                    target_module, target_name = cross_target
                    target_path = modules[target_module][0]
                    pending_cross_file.append((call_id, ref["id"], relative, target_path, target_name))

    for call_id, evidence_id, relative, target_path, target_name in pending_cross_file:
        target_id = definition_ids.get((target_path, target_name))
        if not target_id:
            continue
        edges.append({
            "id": stable("edge", f"{call_id}:resolves-symbol:{target_id}"),
            "from": call_id,
            "to": target_id,
            "type": "resolves-symbol",
            "evidence": [evidence_id],
            "attributes": {"resolution": "same-tree-from-import", "target": f"{target_path}:{target_name}"},
        })
    diagnostics.append({"code": "python-ast-boundary", "severity": "info", "message": "static AST facts only; runtime dispatch, aliases, re-exports, rebinding, nested scopes, relative or dynamic imports, and dynamic calls remain unresolved"})
    diagnostics.append({"code": "python-source-context-boundary", "severity": "info",
                        "message": "Statements and enclosing if/loop/exception/context-manager/match regions are lexical source facts; no reachability, data flow, handler matching, runtime order or success is inferred. Scope boundaries are not crossed when attaching enclosing statements to calls. Excerpts may be truncated with explicit flags."})
    return {"schema": "skill-lens.graph-patch.v0.1", "provider": PROVIDER, "revision": revision, "caseId": case_id, "nodes": nodes, "edges": edges, "evidence": evidence, "diagnostics": diagnostics}


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
    print(f"{args.case_id}: python_files={result['nodes'][0]['attributes']['pythonFileCount']} nodes={len(result['nodes'])} edges={len(result['edges'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
