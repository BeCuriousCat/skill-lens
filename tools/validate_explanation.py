"""Fail closed on fabricated citations and source/runtime layer confusion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _validate_shape(value):
    required = ('schema', 'caseId', 'revision', 'graphSha256', 'question', 'summary',
                'status', 'documentAnalysis', 'investigation', 'claims', 'mechanismSteps', 'traceSearch',
                'comparisons', 'unknowns', 'runContext', 'semantics')
    missing = [key for key in required if key not in value]
    if missing: raise ValueError('explanation schema missing: ' + ', '.join(missing))
    if value['schema'] != 'skill-lens.explanation-chain.v0.1': raise ValueError('explanation schema mismatch')
    if not isinstance(value['claims'], list) or not isinstance(value['comparisons'], list):
        raise ValueError('claims and comparisons must be arrays')
    if not isinstance(value['mechanismSteps'], list):
        raise ValueError('mechanismSteps must be an array')
    if 'decisionRules' in value and not isinstance(value['decisionRules'], list):
        raise ValueError('decisionRules must be an array')
    if value['status'] not in ('static-only', 'trace-found'):
        raise ValueError('unsupported explanation status')
    for key in ('documentAnalysis', 'investigation', 'traceSearch', 'semantics'):
        if not isinstance(value[key], dict): raise ValueError(key + ' must be an object')
    if value['traceSearch'].get('status') not in ('disabled', 'not-requested', 'found', 'not-found'):
        raise ValueError('unsupported trace search status')
    selection = value['traceSearch'].get('selection')
    if not isinstance(selection, dict) or not isinstance(selection.get('decision'), str):
        raise ValueError('trace search selection decision missing')
    if not isinstance(value['runContext'], dict): raise ValueError('runContext must be an object')
    if value['runContext'].get('analysisStatus') not in ('complete', 'partial', None):
        raise ValueError('unsupported analysis status')
    translation = value.get('translation')
    if translation is not None:
        if not isinstance(translation, dict) or translation.get('schema') != 'skill-lens.translation.v0.1':
            raise ValueError('invalid translation metadata')
        if translation.get('locale') != 'zh-CN' or translation.get('mode') != 'bilingual':
            raise ValueError('translation metadata must declare zh-CN bilingual mode')
        if translation.get('status') not in ('available', 'partial', 'unavailable'):
            raise ValueError('unsupported translation metadata status')


def validate_explanation(value, bundle):
    _validate_shape(value)
    raw = (Path(bundle) / 'evidence-graph.json').read_bytes()
    graph = json.loads(raw)
    if value.get('schema') != 'skill-lens.explanation-chain.v0.1': raise ValueError('explanation schema mismatch')
    for key in ('caseId', 'revision'):
        if value.get(key) != graph.get(key): raise ValueError('explanation identity mismatch: ' + key)
    if value.get('graphSha256') != hashlib.sha256(raw).hexdigest(): raise ValueError('explanation graph hash mismatch')
    nodes = {n['id']: n for n in graph['nodes']}
    evidence = {e['id']: e for e in graph['evidence']}
    def cited(item, require=True):
        ids = item.get('evidenceNodeIds', [])
        if require and not ids: raise ValueError('claim without source evidence')
        for nid in ids:
            if nid not in nodes or not nodes[nid].get('evidence'): raise ValueError('unknown or unsupported source node')
        if 'sourceQuote' in item and not any(item['sourceQuote'] in evidence[eid].get('quote', '') for nid in ids for eid in nodes[nid]['evidence'] if eid in evidence):
            raise ValueError('sourceQuote absent from cited source')
    for rule in value['documentAnalysis']['rules']: cited(rule)
    for claim in value['claims']:
        cited(claim)
        if claim.get('evidenceType') not in ('source-declared', 'source-structural', 'analyst-interpreted'):
            raise ValueError('source claim incorrectly promoted to runtime')
        if claim.get('traceEventRefs'): raise ValueError('static claim contains unsupported runtime references')
        if claim.get('translationStatus') not in (None, 'machine-translated', 'human-reviewed', 'unavailable'):
            raise ValueError('unsupported claim translation status')
    for step in value['mechanismSteps']:
        required = ('id', 'title', 'input', 'decision', 'action', 'observable',
                    'evidenceNodeIds', 'status', 'boundary')
        if any(not isinstance(step.get(key), str) or not step[key].strip()
               for key in required if key != 'evidenceNodeIds'):
            raise ValueError('mechanism step has incomplete fields')
        if step.get('status') not in ('source-supported', 'inferred-link', 'unconfirmed'):
            raise ValueError('unsupported mechanism step status')
        if not isinstance(step.get('evidenceNodeIds'), list):
            raise ValueError('mechanism step evidenceNodeIds must be an array')
        if step['status'] == 'unconfirmed' and step['evidenceNodeIds']:
            raise ValueError('unconfirmed mechanism step cannot cite source nodes')
        for nid in step['evidenceNodeIds']:
            if nid not in nodes or not nodes[nid].get('evidence'):
                raise ValueError('mechanism step cites unknown or unsupported source node')
    for rule in value.get('decisionRules', []):
        required = ('id', 'title', 'scenario', 'decision', 'action', 'status', 'evidenceNodeIds')
        if any(not isinstance(rule.get(key), str) or not rule[key].strip()
               for key in required if key != 'evidenceNodeIds'):
            raise ValueError('decision rule has incomplete fields')
        if rule.get('status') not in ('source-supported', 'inferred-link', 'unconfirmed'):
            raise ValueError('unsupported decision rule status')
        if not isinstance(rule.get('evidenceNodeIds'), list):
            raise ValueError('decision rule evidenceNodeIds must be an array')
        if rule['status'] == 'unconfirmed' and rule['evidenceNodeIds']:
            raise ValueError('unconfirmed decision rule cannot cite source nodes')
        for nid in rule['evidenceNodeIds']:
            if nid not in nodes or not nodes[nid].get('evidence'):
                raise ValueError('decision rule cites unknown or unsupported source node')
    trace_ids, event_refs = set(), set()
    for slice_ in value['traceSearch']['slices']:
        tid = slice_['traceId']
        if tid in trace_ids: raise ValueError('duplicate trace ID')
        trace_ids.add(tid)
        local = set()
        for event in slice_['events']:
            if event['id'] in local: raise ValueError('duplicate event ID')
            local.add(event['id']); event_refs.add((tid, event['id']))
            if not event.get('locator', {}).get('recordSha256'): raise ValueError('runtime event has no immutable locator')
        for edge in slice_['edges']:
            if edge['from'] not in local or edge['to'] not in local or edge['basis'] != 'explicit-call-id':
                raise ValueError('unsupported runtime edge')
    for comparison in value['comparisons']:
        if (comparison['traceId'], comparison['eventId']) not in event_refs: raise ValueError('unknown runtime event')
        if comparison.get('sourceMatch') not in ('unverified', 'candidate-overlap', 'matched-by-explicit-evidence'):
            raise ValueError('unsupported source/runtime match status')
        if comparison.get('sourceMatch') == 'matched-by-explicit-evidence' and not comparison.get('evidenceNodeIds'):
            raise ValueError('explicit runtime match has no source evidence')
    if not value.get('unknowns'): raise ValueError('static/runtime limits missing')
    if value['investigation']['sufficiency']['status'] == 'static-sufficient' and value['traceSearch']['slices']:
        raise ValueError('trace read despite sufficient static evidence')
    return {'schema': 'skill-lens.explanation-review.v0.1', 'status': 'evidence-gate-passed',
            'graphSha256': value['graphSha256'], 'sourceClaimCount': len(value['claims']),
            'traceEventCount': len(event_refs), 'semanticCorrectness': 'requires-source-aware-review',
            'readerComprehension': 'not-evaluated'}
