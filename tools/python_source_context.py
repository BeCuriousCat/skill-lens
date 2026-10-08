"""Source-visible Python statements and enclosing lexical regions only.

No reachability, dominance, value propagation, handler matching or runtime
order is inferred. Expression text preserves literal whitespace.
"""

import ast


STATEMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Return,
              ast.Raise, ast.Assert, ast.Delete)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
          ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
TRY_NODES = (ast.Try, getattr(ast, "TryStar", ast.Try))


def is_source_statement(node):
    return isinstance(node, STATEMENTS) and not (
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str))


def nearest_source_statement(node, parents):
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, SCOPES):
            return None
        if is_source_statement(parent):
            return parent
        parent = parents.get(parent)
    return None


def location(node):
    return {"startLine": node.lineno, "endLine": node.end_lineno}


def source_text(node, text):
    return ast.get_source_segment(text, node) or ""


def bounded_source(node, text, key):
    value = source_text(node, text)
    return {key: value[:240], key + "Truncated": len(value) > 240,
            **location(node)}


def context_manager_item(item, text):
    binding = source_text(item.optional_vars, text) if item.optional_vars else None
    return {**bounded_source(item.context_expr, text, "expression"),
            "binding": binding[:240] if binding is not None else None,
            "bindingTruncated": binding is not None and len(binding) > 240}


def lexical_names(node):
    """Literal name mentions, not reference or reaching-definition resolution."""
    loads, stores = set(), set()
    def visit(item):
        if isinstance(item, SCOPES):
            return
        if isinstance(item, ast.Name):
            if isinstance(item.ctx, ast.Load):
                loads.add(item.id)
            elif isinstance(item.ctx, ast.Store):
                stores.add(item.id)
        for child in ast.iter_child_nodes(item):
            visit(child)
    visit(node)
    return loads, stores


def scope_position(node, parents):
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, SCOPES):
            return f"{type(parent).__name__}:{parent.lineno}:{parent.col_offset}-{parent.end_lineno}:{parent.end_col_offset}"
        parent = parents.get(parent)
    return "module"


def source_statement_fact(node, text, parents):
    context = []
    reads, writes = lexical_names(node)
    child = node
    parent = parents.get(child)
    while parent is not None:
        if isinstance(parent, SCOPES):
            break
        if isinstance(parent, ast.If):
            reads.update(lexical_names(parent.test)[0])
            branch = "then" if child in parent.body else "else" if child in parent.orelse else "condition"
            predicate = bounded_source(parent.test, text, "condition")
            predicate["truncated"] = predicate.pop("conditionTruncated")
            context.append({"kind": "if", "branch": branch, **predicate})
        elif isinstance(parent, (ast.For, ast.AsyncFor, ast.While)):
            reads.update(lexical_names(parent.test if isinstance(parent, ast.While) else parent.iter)[0])
            branch = "body" if child in parent.body else "else" if child in parent.orelse else "header"
            entry = {"kind": "loop", "syntax": type(parent).__name__, "branch": branch}
            if isinstance(parent, ast.While):
                entry.update(bounded_source(parent.test, text, "condition"))
            else:
                entry.update(bounded_source(parent.iter, text, "iterable"))
                binding = source_text(parent.target, text)
                entry.update(binding=binding[:240], bindingTruncated=len(binding) > 240)
            context.append(entry)
        elif isinstance(parent, TRY_NODES):
            region = ("try" if child in parent.body else "else" if child in parent.orelse
                      else "finally" if child in parent.finalbody else "catch"
                      if isinstance(child, ast.ExceptHandler) and child in parent.handlers else "unknown")
            # Record the header location, not the full region as though it were
            # one source quote or proof of every statement's execution.
            line = child.lineno if isinstance(child, ast.ExceptHandler) else parent.lineno
            entry = {"kind": "exception-region", "syntax": type(parent).__name__,
                     "region": region, "startLine": line, "endLine": line}
            if isinstance(child, ast.ExceptHandler):
                if child.type is not None:
                    entry.update(bounded_source(child.type, text, "exceptionType"))
                if child.name:
                    entry["binding"] = child.name
            context.append(entry)
        elif isinstance(parent, (ast.With, ast.AsyncWith)):
            context.append({"kind": "context-manager", "syntax": type(parent).__name__,
                            "items": [context_manager_item(item, text) for item in parent.items],
                            "startLine": parent.lineno,
                            "endLine": max(item.context_expr.end_lineno for item in parent.items)})
        elif isinstance(parent, ast.match_case):
            entry = {"kind": "match-case", **bounded_source(parent.pattern, text, "pattern")}
            if parent.guard is not None:
                reads.update(lexical_names(parent.guard)[0])
                guard = bounded_source(parent.guard, text, "guard")
                entry.update(guard=guard["guard"], guardTruncated=guard["guardTruncated"],
                             endLine=max(entry["endLine"], guard["endLine"]))
            context.append(entry)
        elif isinstance(parent, ast.Match):
            reads.update(lexical_names(parent.subject)[0])
            context.append({"kind": "match", **bounded_source(parent.subject, text, "subject")})
        child, parent = parent, parents.get(parent)
    context.reverse()
    expression = source_text(node, text)
    span = location(node)
    start = min([span["startLine"], *(entry["startLine"] for entry in context)])
    end = max([span["endLine"], *(entry["endLine"] for entry in context)])
    position = f"{node.lineno}:{node.col_offset}-{node.end_lineno}:{node.end_col_offset}"
    context_text = context_label(context)
    quote = (f"lexical context {context_text}; " if context else "") + f"source statement at {position}: {expression}"
    return {
        "attributes": {"kind": "source-statement", "language": "python",
                       "statementKind": type(node).__name__, "expression": expression[:400],
                       "expressionTruncated": len(expression) > 400,
                       "lexicalContext": context, "staticOnly": True,
                       "statementSpan": position,
                       "sourceScope": scope_position(node, parents),
                       "readNames": sorted(reads), "writtenNames": sorted(writes),
                       "statementLines": f"{span['startLine']}-{span['endLine']}",
                       "evidenceExcerptTruncated": len(quote) > 500},
        "startLine": start, "endLine": end, "quote": quote,
    }


def context_label(context):
    parts = []
    for entry in context:
        kind = entry["kind"]
        if kind == "if":
            label = f"if {entry['branch']}: {entry['condition']}"
        elif kind == "loop":
            label = f"{entry['syntax']} {entry['branch']}: {entry.get('condition', entry.get('iterable'))}"
        elif kind == "exception-region":
            label = f"{entry['syntax']} {entry['region']} {entry.get('exceptionType', '')} {entry.get('binding', '')}"
        elif kind == "context-manager":
            label = entry['syntax'] + " " + ", ".join(item['expression'] for item in entry['items'])
        elif kind == "match":
            label = f"match {entry['subject']}"
        else:
            label = f"match case {entry['pattern']}" + (f" if {entry['guard']}" if 'guard' in entry else '')
        parts.append(f"{label} ({entry['startLine']}-{entry['endLine']})")
    return "; ".join(parts)
