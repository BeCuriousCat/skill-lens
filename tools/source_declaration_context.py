"""Bounded lexical declaration companions; never reaching-definition edges."""

import re


def statement_position(value):
    match = re.fullmatch(r'(\d+):(\d+)-(\d+):(\d+)', str(value))
    return tuple(map(int, match.groups())) if match else None


def declaration_context(graph, evidence_by_id):
    """Select preceding assignments mentioning loaded names in the same scope.

    Ambiguous assignments are left out. This supplies source context only;
    a preceding assignment is not proved to reach a use or to execute at all.
    No module lookup, parameter resolution, data flow or graph edge is added.
    """
    scopes = {}
    statements = []
    for node in graph.get('nodes', []):
        attrs = node.get('attributes', {})
        if attrs.get('kind') != 'source-statement' or attrs.get('language') != 'python':
            continue
        pos = statement_position(attrs.get('statementSpan'))
        if not pos or not isinstance(attrs.get('sourceScope'), str):
            continue
        for ref_id in node.get('evidence', []):
            ref = evidence_by_id.get(ref_id, {})
            source = ref.get('source', {})
            if ref.get('sourceType') != 'code' or not source.get('path'):
                continue
            scope = (source.get('repository'), source.get('revision'), source['path'], attrs['sourceScope'])
            entry = (pos, node, ref_id)
            scopes.setdefault(scope, []).append(entry)
            statements.append((scope, entry))
    result = {}
    for scope, (position, node, ref_id) in statements:
        selected, problems = [], []
        frontier = [node]
        seen = {node['id']}
        for _ in range(2):
            next_frontier = []
            for item in frontier:
                attrs = item['attributes']
                item_pos = statement_position(attrs['statementSpan'])
                names = attrs.get('readNames', [])
                if not isinstance(names, list):
                    continue
                for name in sorted(set(value for value in names if isinstance(value, str))):
                    matches = {}
                    for pos, candidate, candidate_ref in scopes[scope]:
                        facts = candidate['attributes']
                        if (pos[:2] < item_pos[:2] and facts.get('statementKind') in {'Assign', 'AnnAssign'}
                                and name in facts.get('writtenNames', [])):
                            matches.setdefault(candidate['id'], (candidate, candidate_ref))
                    if len(matches) > 1:
                        problems.append({'name': name, 'reason': 'multiple preceding assignments; no binding resolution'})
                        continue
                    if not matches:
                        continue
                    candidate, candidate_ref = next(iter(matches.values()))
                    if candidate['id'] in seen:
                        continue
                    if len(selected) >= 4:
                        problems.append({'name': name, 'reason': 'declaration companion count limit'})
                        continue
                    seen.add(candidate['id'])
                    selected.append((candidate['id'], candidate_ref))
                    next_frontier.append(candidate)
            frontier = next_frontier
        for item in frontier:
            attrs = item['attributes']
            item_pos = statement_position(attrs['statementSpan'])
            for name in attrs.get('readNames', []):
                if any(pos[:2] < item_pos[:2] and candidate['id'] not in seen
                       and candidate['attributes'].get('statementKind') in {'Assign', 'AnnAssign'}
                       and name in candidate['attributes'].get('writtenNames', [])
                       for pos, candidate, _ in scopes[scope]):
                    problems.append({'name': name, 'reason': 'declaration companion depth limit; further assignments not explored'})
        if selected or problems:
            selected.sort(key=lambda pair: statement_position(
                next(entry[1]['attributes']['statementSpan'] for entry in scopes[scope] if entry[1]['id'] == pair[0])))
            result[(node['id'], ref_id)] = {'companions': selected, 'gaps': problems}
    return result
