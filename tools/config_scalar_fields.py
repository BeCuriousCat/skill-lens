"""Source-visible config scalars; no schema interpretation or config loading."""
from __future__ import annotations

import json
import re
from collections import Counter


SENSITIVE = re.compile(r"api[_-]?key|token|secret|authorization|cookie|password|credential|private[_-]?key", re.I)


def fields(root, content: bytes, language: str) -> tuple[list[dict], list[str]]:
    records, gaps = [], set()
    declarations = Counter()

    def raw(node):
        return content[node.start_byte:node.end_byte].decode('utf-8')

    def key(node):
        text = raw(node)
        if node.type in {'bare_key', 'string_scalar'} or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', text):
            return text
        if text.startswith('"'):
            try:
                value = json.loads(text)
                return value if isinstance(value, str) else None
            except ValueError:
                return None
        if text.startswith("'") and text.endswith("'"):
            return text[1:-1].replace("''", "'")
        return None

    def unwrap(node):
        while node is not None and node.type in {'flow_node', 'block_node', 'plain_scalar'}:
            children = [child for child in node.named_children if child.type != 'comment']
            if len(children) != 1:
                return None
            node = children[0]
        return node

    def emit(pair, value, path):
        syntax = raw(value)
        if len(syntax) > 2000:
            gaps.add('oversized-scalar')
            return
        records.append({'keyPath': list(path), 'rawValue': syntax,
                        'valueSyntaxType': value.type, 'valueEncoding': 'source-syntax',
                        'startLine': pair.start_point[0] + 1, 'endLine': pair.end_point[0] + 1,
                        'startByte': pair.start_byte})

    def walk(node, prefix=()):
        if node is None:
            gaps.add('unsupported-key-or-value')
            return
        if language == 'json':
            if node.type in {'document', 'object'}:
                for child in node.named_children:
                    if child.type != 'comment': walk(child, prefix)
            elif node.type == 'pair':
                k, value = node.child_by_field_name('key'), node.child_by_field_name('value')
                name = key(k) if k else None
                if name is not None:
                    declarations[(*prefix, name)] += 1
                if name is None:
                    gaps.add('unsupported-key-or-value')
                elif SENSITIVE.search(name):
                    gaps.add('sensitive-fields-omitted')
                elif value and value.type == 'object':
                    walk(value, (*prefix, name))
                elif value and value.type in {'string', 'number', 'true', 'false', 'null'}:
                    emit(node, value, (*prefix, name))
                else:
                    gaps.add('non-scalar-values')
            else:
                gaps.add('non-mapping-document')
        elif language == 'yaml':
            if node.type in {'stream', 'document', 'block_mapping', 'flow_mapping'}:
                for child in node.named_children:
                    if child.type != 'comment': walk(child, prefix)
            elif node.type in {'flow_node', 'block_node'}:
                walk(unwrap(node), prefix)
            elif node.type in {'block_mapping_pair', 'flow_pair'}:
                k, value = node.child_by_field_name('key'), node.child_by_field_name('value')
                k = unwrap(k)
                name = key(k) if k else None
                if name is not None:
                    declarations[(*prefix, name)] += 1
                value = unwrap(value)
                if name is None:
                    gaps.add('unsupported-key-or-value')
                elif SENSITIVE.search(name):
                    gaps.add('sensitive-fields-omitted')
                elif value and value.type in {'block_mapping', 'flow_mapping'}:
                    walk(value, (*prefix, name))
                elif value and value.type in {'string_scalar', 'boolean_scalar', 'integer_scalar', 'float_scalar',
                                             'null_scalar', 'double_quote_scalar', 'single_quote_scalar', 'block_scalar'}:
                    emit(node, value, (*prefix, name))
                else:
                    gaps.add('non-scalar-or-anchored-values')
            else:
                gaps.add('non-mapping-document')
        elif language == 'toml':
            if node.type == 'document':
                for child in node.named_children:
                    if child.type != 'comment': walk(child, prefix)
            elif node.type in {'table', 'pair'}:
                children = [child for child in node.named_children if child.type != 'comment']
                if not children:
                    return
                key_node = children[0]
                parts = key_node.named_children if key_node.type == 'dotted_key' else [key_node]
                names = [key(part) for part in parts]
                if any(name is None for name in names):
                    gaps.add('unsupported-key-or-value')
                    return
                if any(SENSITIVE.search(name) for name in names):
                    gaps.add('sensitive-fields-omitted')
                    return
                path = (*prefix, *names)
                declarations[path] += 1
                if node.type == 'table':
                    for child in children[1:]: walk(child, path)
                elif len(children) == 2 and children[1].type in {'string', 'boolean', 'integer', 'float',
                        'offset_date_time', 'local_date_time', 'local_date', 'local_time'}:
                    emit(node, children[1], path)
                else:
                    gaps.add('non-scalar-values')
            else:
                gaps.add('non-scalar-values')

    if root.has_error:
        return [], ['parse-errors']
    if language == 'yaml' and sum(child.type == 'document' for child in root.named_children) > 1:
        return [], ['multiple-yaml-documents']
    walk(root)
    for record in records:
        path = tuple(record['keyPath'])
        record['duplicatePath'] = any(declarations[path[:length]] > 1 for length in range(1, len(path) + 1))
    if any(count > 1 for count in declarations.values()):
        gaps.add('duplicate-field-paths')
    return records, sorted(gaps)
