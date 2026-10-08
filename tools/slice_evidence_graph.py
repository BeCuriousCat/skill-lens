#!/usr/bin/env python3
"""Build a bounded, source-linked Evidence Graph slice for one question.

Selection is deliberately lexical. The result preserves selected graph nodes,
their Evidence, and edges between selected nodes so a later Projector can use a
small, traceable input without treating retrieval as semantic interpretation.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from source_declaration_context import declaration_context
from document_section_context import section_requests


STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "does", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "the", "this", "to", "what", "when",
    "which", "who", "why", "with", "do", "should", "must", "its", "into", "that",
}
WORDS = re.compile(r"[a-z][a-z0-9_./-]*|[\u4e00-\u9fff]+", re.IGNORECASE)
# Query vocabulary for common source-level operations. These only broaden
# retrieval candidates; they never assert an API's effects or execution.
QUERY_ALIASES = {
    "output": {"out"},
    "save": {"saved", "write", "writefile", "write_file"},
    "saving": {"saved", "write", "writefile", "write_file"},
    "error": {"catch", "finally", "throw", "message"},
    "failure": {"catch", "finally", "throw", "message"},
    "cleanup": {"finally", "unlink", "remove"},
    "authentication": {"authorization", "bearer", "headers"},
    "response": {"bytes", "decode"},
    "multipart": {"append"},
    "formdata": {"append"},
    "validated": {"validate", "validation"},
    "validation": {"validate", "validated"},
    "validating": {"validate", "validation"},
    "allow": {"allowed"},
    "accept": {"accepted", "allowed"},
}


def terms(value: str) -> set[str]:
    result = set()
    for raw in WORDS.findall(str(value)):
        # Preserve qualified identifiers while also matching their parts:
        # output.writeFile should be searchable as output, write and file.
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
        parts = [raw.casefold(), *re.split(r"[\s_./-]+", expanded.casefold())]
        for part in parts:
            word = part.strip("-_.")
            if word in STOP or len(word) <= 1:
                continue
            result.add(word)
            if word.isascii() and len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
                result.add(word[:-1])
    return result


def _flatten(value) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def markdown_example_context(graph: dict, evidence_by_id: dict) -> dict:
    """Pair adjacent prose/example spans in one document and heading scope.

    This adds retrieval context only. It creates no graph relation and does
    not treat a documented example as executable or current configuration.
    """
    spans = {}
    for node in graph.get("nodes", []):
        attrs = node.get("attributes", {})
        if attrs.get("kind") != "markdown-span":
            continue
        heading = attrs.get("headingPath", [])
        if not isinstance(heading, list) or not all(isinstance(part, str) for part in heading):
            continue
        for evidence_id in node.get("evidence", []):
            ref = evidence_by_id.get(evidence_id, {})
            source = ref.get("source", {})
            match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", str(source.get("lines", "")))
            if not match or not source.get("path"):
                continue
            start, end = map(int, match.groups())
            if end < start:
                continue
            scope = (source.get("repository"), source.get("revision"), source["path"], tuple(heading))
            spans.setdefault(scope, []).append((start, end, node["id"], evidence_id, attrs.get("blockType")))
    pairs = {}
    for values in spans.values():
        values.sort()
        for before, after in zip(values, values[1:]):
            if (before[4] == "paragraph_open" and after[4] in {"fence", "code_block"}
                    and 1 <= after[0] - before[1] <= 3):
                intro, example = before[2:4], after[2:4]
                pairs[intro] = {"pair": example, "role": "introduction"}
                pairs[example] = {"pair": intro, "role": "example"}
    return pairs


def inventory_quote(ref: dict) -> bool:
    """Recognize redundant file-existence evidence, not arbitrary source facts."""
    if ref.get("sourceType") not in {"code", "doc"}:
        return False
    return bool(re.fullmatch(r"(?:syntax file|Markdown file|Python file|file) .+ exists(?: in the locked source tree)?",
                             ref.get("quote", ""), flags=re.IGNORECASE))


def markdown_covers(container: dict, item: dict) -> bool:
    """Exact source excerpt containment, not semantic equivalence."""
    a, b = container.get('attributes', {}), item.get('attributes', {})
    if a.get('kind') != 'markdown-span' or b.get('kind') != 'markdown-span':
        return False
    if a.get('headingPath') != b.get('headingPath'):
        return False
    left, right = container.get('source', {}), item.get('source', {})
    if any(left.get(key) != right.get(key) for key in ('path', 'revision', 'repository')):
        return False
    def interval(source):
        match = re.fullmatch(r'([1-9][0-9]*)-([1-9][0-9]*)', str(source.get('lines', '')))
        return tuple(map(int, match.groups())) if match else None
    outer, inner = interval(left), interval(right)
    return bool(outer and inner and outer[0] <= inner[0] <= inner[1] <= outer[1]
                and item.get('quote', '').strip() and item['quote'].strip() in container.get('quote', ''))


def markdown_prose_context(graph: dict, evidence_by_id: dict) -> dict:
    """Keep adjacent preceding qualifying prose in one document/heading.

    This can retain an exception before a workflow instruction. It does not
    classify the prose semantically or infer that a workflow executes.
    """
    scopes = {}
    for node in graph.get('nodes', []):
        attrs = node.get('attributes', {})
        heading = attrs.get('headingPath')
        if (attrs.get('kind') != 'markdown-span' or not isinstance(heading, list)
                or not all(isinstance(value, str) for value in heading)):
            continue
        for ref_id in node.get('evidence', []):
            source = evidence_by_id.get(ref_id, {}).get('source', {})
            match = re.fullmatch(r'([1-9][0-9]*)-([1-9][0-9]*)', str(source.get('lines', '')))
            if not match or not source.get('path'):
                continue
            start, end = map(int, match.groups())
            if end < start:
                continue
            scope = (source.get('repository'), source.get('revision'), source['path'], tuple(heading))
            scopes.setdefault(scope, []).append((start, end, node['id'], ref_id, attrs.get('blockType')))
    result = {}
    for values in scopes.values():
        values.sort()
        for before, after in zip(values, values[1:]):
            quote = evidence_by_id[before[3]].get('quote', '')
            qualifier = re.search(r'\b(?:skip|unless|except|only (?:if|when)|otherwise)\b', quote, re.IGNORECASE)
            if (qualifier and before[4] == 'paragraph_open'
                    and after[4] in {'paragraph_open', 'bullet_list_open', 'ordered_list_open'}
                    and 1 <= after[0] - before[1] <= 3):
                result[after[2:4]] = before[2:4]
    return result


def enrich_primary_slice(graph: dict, primary: dict, context_budget_chars: int = 0,
                         include_document_sections: bool = False) -> dict:
    """Retain primary literal source coverage before optional context.

    Only exact contained Markdown excerpts can be compacted. Freed capacity
    is spent on existing lexical context facts, never new retrieval guesses.
    """
    evidence = {ref['id']: ref for ref in graph.get('evidence', [])}
    nodes = {node['id']: node for node in graph.get('nodes', [])}
    declarations = declaration_context(graph, evidence)
    prose = markdown_prose_context(graph, evidence)
    def covers(container, item):
        # Source observations are not interchangeable with documentation,
        # even when they carry the same literal text and source location.
        return (evidence[container['evidenceId']].get('sourceType') == 'doc'
                and evidence[item['evidenceId']].get('sourceType') == 'doc'
                and markdown_covers(container, item))
    selected = []
    covered = []
    for item in primary['selected']:
        containers = [other for other in primary['selected']
                      if (other['nodeId'], other['evidenceId']) != (item['nodeId'], item['evidenceId'])
                      and covers(other, item)]
        # Equal excerpts have a deterministic representative; strict larger
        # containers retain the full literal source text of every child.
        containers = [other for other in containers
                      if not covers(item, other)
                      or (other['nodeId'], other['evidenceId']) < (item['nodeId'], item['evidenceId'])]
        if containers:
            covered.append(item)
        else:
            selected.append(item)
    pairs = {(item['nodeId'], item['evidenceId']) for item in selected}
    used = sum(len(json.dumps(item, ensure_ascii=False)) for item in selected)
    primary_used = used
    compacted_used = used
    total_budget = primary['budgetChars'] + context_budget_chars
    def item_for(pair):
        node, ref = nodes[pair[0]], evidence[pair[1]]
        return {'nodeId': node['id'], 'evidenceId': ref['id'], 'nodeType': node.get('type'),
                'nodeLabel': node.get('label'), 'attributes': node.get('attributes', {}),
                'source': ref.get('source', {}), 'quote': ref.get('quote', ''),
                'confidence': ref.get('confidence')}
    def retained(pair):
        return pair in pairs or any(covers(item, item_for(pair)) for item in selected)
    requests = []
    for item in primary['selected']:
        pair = item['nodeId'], item['evidenceId']
        if not retained(pair):
            continue
        for related in declarations.get(pair, {}).get('companions', []):
            requests.append((pair, related, 'source-declaration'))
        if pair in prose:
            requests.append((pair, prose[pair], 'markdown-prose'))
    section_gaps = []
    if include_document_sections:
        section_pairs, section_gaps = section_requests(graph, primary, primary['question'], terms)
        requests.extend((pair, pair, 'markdown-section') for pair in section_pairs)
    # Document-section context is optional and must never crowd out primary
    # question evidence. Process non-section companions first, then section
    # pairs in their ranked order; this makes the budget behavior deterministic
    # while preserving the existing primary literal-retention contract.
    section_items = [item for item in requests if item[2] == 'markdown-section']
    non_section_requests = [item for item in requests if item[2] != 'markdown-section']
    # Select one representative span per heading scope before expanding any
    # section. This gives each chosen document section a visible anchor under
    # a small node budget while retaining the generic maxNodes invariant.
    representatives = []
    seen_scopes = set()
    for item in section_items:
        source = item_for(item[1]).get('source', {})
        attrs = item_for(item[1]).get('attributes', {})
        scope = (source.get('repository'), source.get('revision'), source.get('path'),
                 tuple(attrs.get('headingPath', [])))
        if scope not in seen_scopes:
            seen_scopes.add(scope)
            representatives.append(item)
    remaining_sections = [item for item in section_items if item not in representatives]
    preferred_markers = set()
    question_text = primary.get('question', '').casefold()
    if any(word in question_text for word in ('test', 'eval', '测试', '评估')):
        preferred_markers |= {'eval', 'test', 'grading', 'assertion'}
    if any(word in question_text for word in ('run', 'pair', 'baseline', '运行', '对照')):
        preferred_markers |= {'run', 'baseline', 'without_skill', 'old_skill', 'subagent'}
    if any(word in question_text for word in ('output', 'path', '输出', '目录')):
        preferred_markers |= {'output', 'outputs', 'path', 'grading'}
    priority_sections = []
    if preferred_markers:
        for item in section_items:
            quote = item_for(item[1]).get('quote', '').casefold()
            if any(marker in quote for marker in preferred_markers):
                priority_sections.append(item)
    ordered_requests = non_section_requests + priority_sections + representatives + remaining_sections
    if preferred_markers:
        # When a question asks about a documented workflow, prefer Markdown
        # evidence over lexical structural summaries for the same source
        # location. The structural node remains available through the source
        # graph, but should not consume the bounded document-context budget.
        doc_pairs = {item[1] for item in section_items}
        ordered_requests = [item for item in ordered_requests
                            if not (item[2] != 'markdown-section'
                                    and item[1] in doc_pairs)] + [item for item in ordered_requests
                                                                    if item[2] == 'markdown-section']
    for pair, related, kind in ordered_requests:
        if retained(related):
            continue
        item = item_for(related)
        cost = len(json.dumps(item, ensure_ascii=False))
        # An anchored section span is already bounded by the selected heading
        # group. Permit its complete paragraph to use the separate context
        # allowance even when it exceeds the primary per-item quarter cap.
        anchored_section = kind == 'markdown-section' and any(
            item.get('source', {}).get('path') == existing.get('source', {}).get('path')
            and item.get('attributes', {}).get('headingPath') == existing.get('attributes', {}).get('headingPath')
            for existing in primary['selected'])
        context_used = used - primary_used
        if ((not anchored_section and cost > primary['budgetChars'] // 4)
                or (kind == 'markdown-section' and context_used + cost > context_budget_chars)
                or (kind != 'markdown-section' and used + cost > total_budget)
                or len(selected) >= primary['maxNodes']):
            continue
        selected.append(item)
        pairs.add(related)
        used += cost
    gaps = section_gaps + [gap for gap in primary.get('contextGaps', [])
            if not gap.get('relatedNodeId') or not retained((gap['relatedNodeId'], gap['relatedEvidenceId']))]
    for pair, related, kind in ordered_requests:
        if not retained(related):
            gaps.append({'nodeId': pair[0], 'evidenceId': pair[1], 'kind': kind,
                         'relatedNodeId': related[0], 'relatedEvidenceId': related[1],
                         'reason': 'context excluded after retaining primary source coverage; reaching definitions and execution unknown'})
    for item in primary['selected']:
        pair = item['nodeId'], item['evidenceId']
        for gap in declarations.get(pair, {}).get('gaps', []):
            gaps.append({'nodeId': pair[0], 'evidenceId': pair[1], 'kind': 'source-declaration', **gap})
    selected_ids = {item['nodeId'] for item in selected}
    return {**primary, 'contextMode': 'document-sections' if include_document_sections else 'source-companions',
            'selected': selected, 'usedChars': used,
            'budgetChars': total_budget, 'primaryBudgetChars': primary['budgetChars'],
            'contextBudgetChars': context_budget_chars, 'contextAddedChars': used - compacted_used,
            'compactedPrimaryChars': primary['usedChars'] - compacted_used,
            'selectedNodeIds': sorted(selected_ids), 'contextGaps': gaps,
            'edges': [edge for edge in graph.get('edges', [])
                      if edge.get('from') in selected_ids and edge.get('to') in selected_ids],
            'compactedPrimaryPairs': [{'nodeId': item['nodeId'], 'evidenceId': item['evidenceId']}
                                     for item in covered],
            'contextPolicy': 'retained-primary-literals-v0.1',
            'boundary': 'Primary literal source coverage is retained; optional same-scope context does not establish reaching definitions, runtime execution or semantic answerability.'}


def slice_graph(graph: dict, question: str, budget_chars: int = 6000, max_nodes: int = 24,
                context_mode: str = 'minimal', context_budget_chars: int = 0) -> dict:
    if budget_chars <= 0 or max_nodes <= 0:
        raise ValueError("budget_chars and max_nodes must be positive")
    if context_mode not in {'minimal', 'source-companions', 'document-sections'}:
        raise ValueError('unsupported context_mode')
    if not isinstance(context_budget_chars, int) or context_budget_chars < 0:
        raise ValueError('context_budget_chars must be a nonnegative integer')
    if context_mode == 'minimal' and context_budget_chars:
        raise ValueError('context budget requires source-companions mode')
    if context_mode in {'source-companions', 'document-sections'}:
        primary = slice_graph(graph, question, budget_chars, max_nodes, context_mode='minimal')
        return enrich_primary_slice(graph, primary, context_budget_chars,
                                    include_document_sections=context_mode == 'document-sections')
    evidence_by_id = {item["id"]: item for item in graph.get("evidence", [])}
    nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
    example_context = markdown_example_context(graph, evidence_by_id)
    prose_context = markdown_prose_context(graph, evidence_by_id) if context_mode == 'source-companions' else {}
    declaration_companions = declaration_context(graph, evidence_by_id) if context_mode == 'source-companions' else {}
    statement_parents = {}
    for edge in graph.get("edges", []):
        parent = nodes_by_id.get(edge.get("from"), {})
        child = nodes_by_id.get(edge.get("to"), {})
        if (edge.get("type") == "contains" and parent.get("attributes", {}).get("kind") == "source-statement"
                and child.get("attributes", {}).get("kind") == "call"):
            statement_parents.setdefault(child["id"], []).append(parent)
    indexed = []
    frequencies = Counter()
    query = terms(question)
    query |= {alias for term in tuple(query) for alias in QUERY_ALIASES.get(term, set())}
    artifact_terms = terms(graph.get("caseId", ""))
    for node in graph.get("nodes", []):
        if node.get("type") == "Artifact":
            artifact_terms |= terms(node.get("label", ""))
    for node in graph.get("nodes", []):
        for evidence_id in node.get("evidence", []):
            ref = evidence_by_id.get(evidence_id)
            if not ref:
                continue
            quote_terms = terms(ref.get("quote", ""))
            context_text = " ".join([
                str(node.get("type", "")),
                str(node.get("label", "")),
                _flatten(node.get("attributes", {})),
                _flatten(node.get("extensions", {})),
                _flatten(ref.get("source", {})),
            ])
            context_terms = terms(context_text)
            frequencies.update(quote_terms | context_terms)
            indexed.append((node, ref, quote_terms, context_terms))

    candidates = []
    for node, ref, quote_terms, context_terms in indexed:
        matched = query & (quote_terms | context_terms)
        if not matched:
            continue
        # A source statement in an explicitly matching file is behavioral
        # source context, rather than just a matching inventory/path string.
        # Prefer its actual guarded code over a dump of syntax/call headers.
        # The artifact's own name is not a file focus: otherwise a generic
        # question about a Skill would prioritize every *_skill helper.
        source_terms = terms(Path(ref.get("source", {}).get("path", "")).stem) - artifact_terms
        statement_file_terms = source_terms if node.get("attributes", {}).get("kind") == "source-statement" else set()
        score = sum(
            math.log1p((len(indexed) + 1) / (frequencies[word] + 1))
            * (3 if word in quote_terms or word in statement_file_terms else 1)
            for word in matched
        )
        if node.get("type") in {"Capability", "Scenario", "Ownership", "Limitation", "Uncertainty"}:
            score *= 1.05
        source = ref.get("source", {})
        line_text = str(source.get("lines", ""))
        try:
            line = int(line_text.split("-", 1)[0])
        except ValueError:
            line = 0
        candidates.append((score, str(source.get("path", "")), line, node["id"], ref["id"], node, ref))
    candidates.sort(key=lambda row: (-row[0], row[1], row[2], row[3], row[4]))

    selected = []
    selected_node_ids = set()
    selected_pairs = set()
    selected_inventory_ids = set()
    used = 0
    def item_for(node, ref):
        return {
            "nodeId": node["id"],
            "evidenceId": ref["id"],
            "nodeType": node.get("type"),
            "nodeLabel": node.get("label"),
            "attributes": node.get("attributes", {}),
            "source": ref.get("source", {}),
            "quote": ref.get("quote", ""),
            "confidence": ref.get("confidence"),
        }
    for _score, _path, _line, node_id, evidence_id, node, ref in candidates:
        if (node_id, evidence_id) in selected_pairs:
            continue
        if node_id in selected_inventory_ids and inventory_quote(ref):
            continue
        if context_mode == 'source-companions' and any(markdown_covers(existing, item_for(node, ref)) for existing in selected):
            continue
        group = []
        # When a call is selected, keep its nearest source statement and
        # lexical guards in the same bounded slice. This is source context,
        # not an inferred runtime dependency or execution trace.
        for parent in sorted(statement_parents.get(node_id, []), key=lambda item: item["id"]):
            for parent_ref in parent.get("evidence", []):
                if parent_ref in evidence_by_id:
                    group.append(item_for(parent, evidence_by_id[parent_ref]))
        group.append(item_for(node, ref))
        mandatory_group = list(group)
        for item in mandatory_group:
            declarations = declaration_companions.get((item['nodeId'], item['evidenceId']), {})
            for related_node_id, related_ref_id in declarations.get('companions', []):
                group.append(item_for(nodes_by_id[related_node_id], evidence_by_id[related_ref_id]))
        preceding = prose_context.get((node_id, evidence_id))
        if preceding:
            group.append(item_for(nodes_by_id[preceding[0]], evidence_by_id[preceding[1]]))
        context = example_context.get((node_id, evidence_id))
        if context and context["role"] == "introduction":
            related_node_id, related_ref_id = context["pair"]
            group.append(item_for(nodes_by_id[related_node_id], evidence_by_id[related_ref_id]))
        unique = {}
        for item in group:
            pair = (item["nodeId"], item["evidenceId"])
            if pair not in selected_pairs:
                unique[pair] = item
        group = list(unique.values())
        if context_mode == 'source-companions':
            group = [item for item in group if not any(markdown_covers(existing, item) for existing in selected)]
        # A larger literal block can replace already selected child paragraphs
        # when it preserves their exact text, location and heading scope.
        replaced = [item for item in selected if any(markdown_covers(other, item) for other in group)] if context_mode == 'source-companions' else []
        reclaimed = sum(len(json.dumps(item, ensure_ascii=False)) for item in replaced)
        costs = [len(json.dumps(item, ensure_ascii=False)) for item in group]
        if (any(cost > budget_chars // 4 for cost in costs) or used - reclaimed + sum(costs) > budget_chars
                or len(selected) - len(replaced) + len(group) > max_nodes):
            # Declaration companions are optional source context; mandatory
            # call guards remain atomic even when the companions cannot fit.
            optional = list(group)
            unique = {(item['nodeId'], item['evidenceId']): item for item in mandatory_group
                      if (item['nodeId'], item['evidenceId']) not in selected_pairs}
            group = list(unique.values())
            costs = [len(json.dumps(item, ensure_ascii=False)) for item in group]
            replaced = []
            reclaimed = 0
            if (any(cost > budget_chars // 4 for cost in costs) or used + sum(costs) > budget_chars
                    or len(selected) + len(group) > max_nodes):
                continue
            included = set(unique)
            for item in optional:
                pair = (item['nodeId'], item['evidenceId'])
                if pair in included or pair in selected_pairs:
                    continue
                cost = len(json.dumps(item, ensure_ascii=False))
                if cost <= budget_chars // 4 and used + sum(costs) + cost <= budget_chars and len(selected) + len(group) < max_nodes:
                    group.append(item)
                    costs.append(cost)
                    included.add(pair)
        for item in replaced:
            selected.remove(item)
            selected_pairs.remove((item['nodeId'], item['evidenceId']))
        used -= reclaimed
        for item, cost in zip(group, costs):
            selected.append(item)
            selected_pairs.add((item["nodeId"], item["evidenceId"]))
            selected_node_ids.add(item["nodeId"])
            if inventory_quote(evidence_by_id[item["evidenceId"]]):
                selected_inventory_ids.add(item["nodeId"])
            used += cost
        selected_node_ids = {item['nodeId'] for item in selected}
        if len(selected) >= max_nodes:
            break

    # When the example itself matched, spend only remaining capacity on its
    # introduction. Its prose need not share the question's vocabulary and
    # must not displace directly matched source facts. Mandatory call guards
    # above still remain an atomic group.
    for item in list(selected):
        context = example_context.get((item["nodeId"], item["evidenceId"]))
        if not context or context["pair"] in selected_pairs:
            continue
        related_node_id, related_ref_id = context["pair"]
        related = item_for(nodes_by_id[related_node_id], evidence_by_id[related_ref_id])
        cost = len(json.dumps(related, ensure_ascii=False))
        if cost > budget_chars // 4 or used + cost > budget_chars or len(selected) + 1 > max_nodes:
            continue
        selected.append(related)
        selected_pairs.add(context["pair"])
        selected_node_ids.add(related_node_id)
        used += cost

    edges = [
        edge for edge in graph.get("edges", [])
        if edge.get("from") in selected_node_ids and edge.get("to") in selected_node_ids
    ]
    return {
        "schema": "skill-lens.evidence-question-slice.v0.1",
        "caseId": graph.get("caseId"),
        "revision": graph.get("revision"),
        "question": question,
        "budgetChars": budget_chars,
        "usedChars": used,
        "maxNodes": max_nodes,
        "contextMode": context_mode,
        "candidateCount": len(candidates),
        "selected": selected,
        "edges": edges,
        "selectedNodeIds": sorted(selected_node_ids),
        "contextGaps": [
            {"nodeId": pair[0], "evidenceId": pair[1],
             "relatedNodeId": context["pair"][0], "relatedEvidenceId": context["pair"][1],
             "kind": "markdown-example", "reason": "related " +
             ("example" if context["role"] == "introduction" else "introduction") + " excluded by bounded selection"}
            for pair, context in sorted(example_context.items())
            if pair in selected_pairs and context["pair"] not in selected_pairs
        ] + [
            {'nodeId': pair[0], 'evidenceId': pair[1], 'kind': 'markdown-prose',
             'relatedNodeId': preceding[0], 'relatedEvidenceId': preceding[1],
             'reason': 'adjacent preceding prose excluded by bounded selection'}
            for pair, preceding in sorted(prose_context.items())
            if pair in selected_pairs and preceding not in selected_pairs
        ] + [
            {'nodeId': pair[0], 'evidenceId': pair[1], 'kind': 'source-declaration',
             'relatedNodeId': related[0], 'relatedEvidenceId': related[1],
             'reason': 'preceding lexical assignment excluded by bounded selection; reaching definition unknown'}
            for pair, context in sorted(declaration_companions.items()) if pair in selected_pairs
            for related in context['companions'] if related not in selected_pairs
        ] + [
            {'nodeId': pair[0], 'evidenceId': pair[1], 'kind': 'source-declaration', **gap}
            for pair, context in sorted(declaration_companions.items()) if pair in selected_pairs
            for gap in context['gaps']
        ],
        "status": "source-candidates" if selected else ("budget-excluded" if candidates else "no-source-candidates"),
        "boundary": "Lexical retrieval only; source-companions mode adds bounded preceding assignments and qualifying prose, with no reaching definitions, execution or semantic answerability established.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("graph", type=Path)
    parser.add_argument("question")
    parser.add_argument("--budget-chars", type=int, default=6000)
    parser.add_argument("--max-nodes", type=int, default=24)
    parser.add_argument('--context-mode', choices=('minimal', 'source-companions', 'document-sections'), default='minimal',
                        help='source-companions is experimental; primary literal coverage is retained before context')
    parser.add_argument('--context-budget-chars', type=int, default=0,
                        help='explicit extra context allowance; total ceiling is budget-chars plus this value')
    args = parser.parse_args()
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    print(json.dumps(slice_graph(graph, args.question, args.budget_chars, args.max_nodes,
                                args.context_mode, args.context_budget_chars), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
