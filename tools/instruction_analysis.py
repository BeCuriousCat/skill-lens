"""Optional, graph-bound interpretation of a Skill's instruction mechanism.

This is an analyst-authored projection, never an automatic runtime claim.
Source quotes are resolved from canonical Markdown spans, not rewritten here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'skill-lens.instruction-analysis.v0.1.schema.json'


def _shape(value, schema, path='analysis'):
    """Validate this small offline schema without an optional Python dependency."""
    kinds = {'object': dict, 'array': list, 'string': str}
    if not isinstance(value, kinds[schema['type']]):
        raise ValueError(f'{path}: expected {schema["type"]}')
    if 'const' in schema and value != schema['const']:
        raise ValueError(f'{path}: schema mismatch')
    if isinstance(value, str):
        if len(value.strip()) < schema.get('minLength', 0):
            raise ValueError(f'{path}: empty text')
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            raise ValueError(f'{path}: invalid identity or hash')
    if isinstance(value, dict):
        properties = schema['properties']
        if set(schema.get('required', [])) - set(value):
            raise ValueError(f'{path}: missing required fields')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError(f'{path}: unsupported fields')
        for key, child in value.items():
            if key in properties:
                _shape(child, properties[key], f'{path}.{key}')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            raise ValueError(f'{path}: item count outside limits')
        if schema.get('uniqueItems') and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError(f'{path}: duplicate items')
        for index, child in enumerate(value):
            _shape(child, schema['items'], f'{path}[{index}]')


def validate_analysis(analysis: dict, graph: dict, graph_bytes: bytes) -> dict:
    _shape(analysis, json.loads(SCHEMA_PATH.read_text(encoding='utf-8')))
    for key in ('caseId', 'revision'):
        if analysis[key] != graph.get(key):
            raise ValueError(f'instruction analysis {key} mismatch')
    if analysis['graphSha256'] != hashlib.sha256(graph_bytes).hexdigest():
        raise ValueError('instruction analysis graph hash mismatch; refresh interpretation for this Graph')
    nodes = {node['id']: node for node in graph['nodes']}
    evidence = {ref['id']: ref for ref in graph['evidence']}
    enriched = dict(analysis)
    def enrich(item, section):
        refs = []
        for node_id in item['evidenceNodeIds']:
            node = nodes.get(node_id)
            if not node or node.get('attributes', {}).get('kind') != 'markdown-span':
                raise ValueError(f'instruction analysis needs an exact Markdown span: {node_id}')
            if not node.get('evidence'):
                raise ValueError(f'instruction analysis node has no Evidence: {node_id}')
            for ref_id in node['evidence']:
                ref = evidence.get(ref_id)
                source = ref.get('source', {}) if ref else {}
                if (not ref or ref.get('sourceType') != 'doc' or not str(ref.get('quote', '')).strip()
                        or source.get('revision') != graph['revision']
                        or not source.get('path') or not re.fullmatch(r'\d+(?:-\d+)?', str(source.get('lines', '')))):
                    raise ValueError(f'instruction analysis needs located source text: {ref_id}')
                # Fences and examples can be quoted as examples, not as
                # instructions governing the Skill's actual behavior.
                if section in ('stages', 'audiences') and node.get('attributes', {}).get('blockType') in ('fence', 'code_block'):
                    raise ValueError(f'example code is not an instruction: {node_id}')
                refs.append(ref_id)
        result = {**item, 'evidenceIds': list(dict.fromkeys(refs))}
        if 'sourceQuote' in item:
            selections = []
            for ref_id in result['evidenceIds']:
                ref = evidence[ref_id]
                offset = ref['quote'].find(item['sourceQuote'])
                if offset < 0:
                    continue
                first_line = int(str(ref['source']['lines']).split('-')[0]) + ref['quote'][:offset].count('\n')
                last_line = first_line + item['sourceQuote'].count('\n')
                selections.append({'evidenceId': ref_id, 'quote': item['sourceQuote'],
                                   'source': {**ref['source'], 'lines': f'{first_line}-{last_line}'}})
            if not selections:
                raise ValueError(f'rule sourceQuote is absent from its cited Evidence: {item["id"]}')
            result['selectedEvidence'] = selections
        if 'rules' in item:
            result['rules'] = [enrich(rule, 'audiences') for rule in item['rules']]
            ids = [rule['id'] for rule in item['rules']]
            if len(set(ids)) != len(ids):
                raise ValueError('duplicate audience rule IDs')
        return result
    for section in ('stages', 'prompts', 'audiences', 'illustrations'):
        enriched[section] = [enrich(item, section) for item in analysis[section]]
    ids = [item['id'] for item in analysis['stages']]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate instruction analysis stage IDs')
    return enriched


def meaning_objects(analysis: dict) -> list[dict]:
    """Addressable analyst concepts, kept separate from canonical Graph nodes."""
    objects = []
    for index, stage in enumerate(analysis['stages']):
        objects.append({**stage, 'id': f'meaning:stage:{index}', 'type': 'Mechanism',
                        'summary': stage['instruction'], 'semantic': True})
    for index, audience in enumerate(analysis['audiences']):
        parent_id = f'meaning:audience:{index}'
        rule_ids = [f'{parent_id}:rule:{rule["id"]}' for rule in audience.get('rules', [])]
        objects.append({**audience, 'id': parent_id, 'type': 'Audience',
                        'summary': audience['instruction'], 'ruleIds': rule_ids, 'semantic': True})
        for rule, rule_id in zip(audience.get('rules', []), rule_ids):
            objects.append({**rule, 'id': rule_id, 'type': 'Rule', 'parentId': parent_id,
                            'summary': rule['instruction'], 'semantic': True})
    return objects


def load_analysis(bundle: Path, path: Path | None = None) -> dict | None:
    explicit = path is not None
    path = path if path is not None else bundle / 'instruction-analysis.json'
    if not path.is_file():
        if explicit:
            raise ValueError(f'instruction analysis file not found: {path}')
        return None
    graph_bytes = (bundle / 'evidence-graph.json').read_bytes()
    return validate_analysis(json.loads(path.read_text(encoding='utf-8')), json.loads(graph_bytes), graph_bytes)
