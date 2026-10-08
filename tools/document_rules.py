"""Source-bound Markdown concepts. Classifications are candidates, not execution facts."""
from __future__ import annotations

import hashlib
import posixpath
import re
from urllib.parse import unquote, urlsplit

ROLE_PATTERNS = {
    'trigger': r'\b(?:use when|trigger|activat|sessionstart|when enabled)\b|触发|启用|当用户',
    'audience': r'\b(?:audience|reader|novice|beginner|manager|engineer|designer|year.old|knows nothing)\b|受众|读者|零基础|经理|工程师|儿童',
    'input': r'\b(?:input|arguments?|parameters?|topic)\b|输入|参数|主题',
    'output-contract': r'\b(?:output|produce|artifact|format|html|deliver|return)\b|输出|生成|交付|格式|返回',
    'tool-policy': r'\b(?:tools?|scripts?|commands?|hook|mcp|invoke|call|execute|run)\b|工具|脚本|调用|执行|命令',
    'prohibition': r'\b(?:must not|never|do not|don.t|forbid|avoid|prohibited)\b|禁止|不得|不要|不能',
    'condition': r'\b(?:if|unless|only when|when|otherwise|before|after)\b|如果|除非|只有|否则|当|之前|之后',
    'constraint': r'\b(?:must|required|should|limit|at most|at least|prefer|keep|jargon|vocabulary|terminology|language|tone|analogy|sentences|pictures|concise|depth|structure)\b|必须|应当|应该|最多|至少|优先|保持|术语|词汇|语气|类比|深度|句子',
    'warning': r'\b(?:warning|caution|risk|note)\b|警告|注意|风险',
}
EXAMPLE = re.compile(r'\b(?:examples?|samples?|demo|illustration)\b|示例|例子|演示', re.I)
DIRECTIVE = re.compile(r'\b(?:must|should|use|read|write|create|generate|explain|identify|select|run|save|keep|avoid|never|do not|follow|ensure|ask|return|produce|include|prefer)\b|请|必须|要求|应当|不要|禁止|使用|解释|输出|选择|读取|生成|执行', re.I)


def stable(prefix, *parts):
    return prefix + ':' + hashlib.sha256('\0'.join(map(str, parts)).encode()).hexdigest()[:20]


def source_records(graph):
    refs = {e['id']: e for e in graph['evidence']}
    records = []
    for node in graph['nodes']:
        attrs = node.get('attributes', {})
        if attrs.get('kind') != 'markdown-span':
            continue
        for eid in node.get('evidence', []):
            e = refs.get(eid, {})
            source = e.get('source', {})
            if e.get('sourceType') != 'doc' or not re.fullmatch(r'\d+(?:-\d+)?', str(source.get('lines', ''))):
                continue
            if source.get('revision') != graph['revision'] or not e.get('quote', '').strip():
                continue
            start, *end = map(int, str(source['lines']).split('-'))
            records.append({'nodeId': node['id'], 'evidenceId': eid, 'path': source['path'],
                            'start': start, 'end': end[0] if end else start, 'quote': e['quote'],
                            'headingPath': attrs.get('headingPath', []), 'blockType': attrs.get('blockType')})
            break
    return sorted(records, key=lambda r: (r['path'], r['start'], r['end'], r['nodeId']))


def classify(record):
    text, heading = record['quote'], ' / '.join(record['headingPath'])
    kind = record['blockType']
    roles, signals = [], []
    if EXAMPLE.search(heading) or kind in ('fence', 'code_block'):
        return ['example'], ['example heading or fenced source; applicability needs semantic review']
    if kind == 'frontmatter' or (record['start'] < 5 and 'name:' in text and 'description:' in text):
        roles.append('identity'); signals.append('frontmatter fields')
    for role, pattern in ROLE_PATTERNS.items():
        if re.search(pattern, text, re.I):
            roles.append(role); signals.append(f'{role}: explicit text signal')
        elif re.search(pattern, heading, re.I):
            roles.append(role); signals.append(f'{role}: heading scope candidate')
    if kind == 'ordered_list_open' or re.search(r'\b(?:step|stage|phase)\b|步骤|阶段', heading, re.I):
        roles.append('workflow-step'); signals.append('numbered list or step heading')
    if re.search(r'\[[^\]]+\]\([^\)]+\)', text):
        roles.append('reference'); signals.append('Markdown link')
    if DIRECTIVE.search(text) and kind != 'heading_open':
        roles.append('instruction'); signals.append('directive vocabulary candidate')
    return list(dict.fromkeys(roles)) or ['unknown'], signals or ['no recognized role; needs semantic review']


def modality(text):
    if re.search(ROLE_PATTERNS['prohibition'], text, re.I): return 'prohibited'
    if re.search(r'\b(?:must|required)\b|必须|要求|应当', text, re.I): return 'required'
    if re.search(r'\b(?:should|prefer|encourage|recommended)\b|建议|应该|优先|鼓励', text, re.I): return 'recommended'
    if re.search(r'\b(?:may|can|optional)\b|可以|可选', text, re.I): return 'permitted'
    return 'unspecified'


def analyze(graph):
    records = source_records(graph)
    units, rules, relations, gaps = [], [], [], []
    paths = {r['path'] for r in records}
    for record in records:
        roles, signals = classify(record)
        units.append({**record, 'roles': roles, 'signals': signals, 'classification': 'candidate'})
        # Container lists duplicate their paragraph children. Keep the container
        # as context; rules come from the leaves. Tables use exact whole rows.
        if record['blockType'] in ('heading_open', 'bullet_list_open', 'ordered_list_open'):
            continue
        if 'example' in roles:
            continue
        quotes = ([line for line in record['quote'].splitlines()
                   if line.strip().startswith('|') and not re.fullmatch(r'[\s|:\-]+', line)]
                  if record['blockType'] == 'table_open' else [record['quote']])
        for quote in quotes:
            if not (DIRECTIVE.search(quote) or set(roles) & {'identity', 'trigger', 'audience', 'input', 'output-contract', 'tool-policy'}):
                continue
            rule_roles, _ = classify({**record, 'quote': quote})
            # The complete paragraph/row preserves nested guards, exceptions and
            # qualifier polarity. We do not invent grammatical subject/action.
            rid = stable('rule', record['nodeId'], quote)
            imperative = re.match(r'\s*(?:[-*+]\s+|\d+\.\s+)?(Use|Read|Write|Create|Generate|Explain|Identify|Select|Run|Save|Keep|Follow|Ensure|Ask|Return|Produce|Include)\s+(.+)', quote, re.I | re.S)
            fields = {'subject': 'addressed agent', 'action': imperative[1], 'object': imperative[2]} if imperative else {'subject': None, 'action': None, 'object': None}
            conditions = [quote] if re.search(ROLE_PATTERNS['condition'], quote, re.I) else []
            exceptions = [quote] if re.search(r'\b(?:unless|except|otherwise)\b|除非|例外|否则', quote, re.I) else []
            rules.append({'id': rid, 'label': re.sub(r'\s+', ' ', quote).strip()[:100],
                          'roles': rule_roles, **fields,
                          'modality': modality(quote), 'conditions': conditions, 'exceptions': exceptions,
                          'sourceQuote': quote, 'evidenceNodeIds': [record['nodeId']],
                          'scope': {'path': record['path'], 'headingPath': record['headingPath']},
                          'classification': 'candidate', 'missingFields': [k for k, v in fields.items() if v is None]})
            if conditions:
                relations.append({'id': stable('relation', rid, 'conditioned-by'), 'from': rid, 'to': record['nodeId'],
                                  'type': 'conditioned-by', 'layer': 'rule-candidate',
                                  'evidenceNodeIds': [record['nodeId']], 'quote': quote})
        for match in re.finditer(r'\[[^\]]+\]\(([^\s)]+)(?:\s+[^)]*)?\)', record['quote']):
            link = urlsplit(match.group(1))
            if link.scheme or link.netloc:
                gaps.append({'nodeId': record['nodeId'], 'reason': 'external-reference', 'target': match.group(1)})
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(record['path']), unquote(link.path))) if link.path else record['path']
            if target not in paths:
                gaps.append({'nodeId': record['nodeId'], 'reason': 'reference-not-in-bundle', 'target': target})
                continue
            targets = [r for r in records if r['path'] == target]
            if link.fragment:
                fragment = unquote(link.fragment).lower()
                scoped = [r for r in targets if any(re.sub(r'[^\w\s-]', '', h.lower()).replace(' ', '-') == fragment for h in r['headingPath'])]
                targets = scoped
            elif link.path:
                # A file link points to its document's first real span as an
                # entry node. The rest of that document remains reachable via
                # the existing source structure and is not flattened here.
                targets = targets[:1]
            if not targets:
                gaps.append({'nodeId': record['nodeId'], 'reason': 'reference-fragment-unresolved', 'target': target})
            for dest in targets:
                relations.append({'id': stable('relation', record['nodeId'], dest['nodeId'], 'references'),
                                  'from': record['nodeId'], 'to': dest['nodeId'], 'type': 'references',
                                  'layer': 'document-reference', 'evidenceNodeIds': [record['nodeId']], 'quote': match.group(0)})
    # Explicit source structure is recorded separately from semantic relations.
    for unit in units:
        containers = [u for u in units if u['path'] == unit['path'] and u['nodeId'] != unit['nodeId']
                      and u['start'] <= unit['start'] and u['end'] >= unit['end']
                      and u['blockType'] in ('bullet_list_open', 'ordered_list_open', 'table_open')]
        headings = [u for u in units if u['path'] == unit['path'] and u['start'] < unit['start']
                    and u['blockType'] == 'heading_open' and unit['headingPath'][:len(u['headingPath'])] == u['headingPath']]
        parent = min(containers, key=lambda u: u['end'] - u['start']) if containers else max(headings, key=lambda u: u['start']) if headings else None
        unit['parentNodeId'] = parent['nodeId'] if parent else None
    groups = {}
    for unit in units: groups.setdefault((unit['path'], unit['parentNodeId']), []).append(unit['nodeId'])
    for unit in units:
        group = groups[unit['path'], unit['parentNodeId']]; pos = group.index(unit['nodeId'])
        unit['previousSiblingId'] = group[pos - 1] if pos else None
        unit['nextSiblingId'] = group[pos + 1] if pos + 1 < len(group) else None
    # Only explicit references to uniquely named sections establish rule links.
    # Generic heading hierarchy stays document structure; it never means override.
    named = {}
    for rule in rules:
        heading = rule['scope']['headingPath']
        if heading: named.setdefault(heading[-1].casefold(), []).append(rule)
    for rule in rules:
        text = rule['sourceQuote']
        for word, relation in [('requires', 'requires'), ('overrides', 'overrides'), ('supersedes', 'overrides'), ('refines', 'refines'), ('illustrates', 'illustrates')]:
            if not re.search(r'\b' + word + r'\b', text, re.I): continue
            for title, targets in named.items():
                if len(targets) != 1 or targets[0]['id'] == rule['id']: continue
                if re.search(r'\b' + word + r'\s+(?:the\s+)?[\"`\']?' + re.escape(title) + r'(?:[\"`\']|\b)', text, re.I):
                    relations.append({'id': stable('relation', rule['id'], targets[0]['id'], relation),
                                      'from': rule['id'], 'to': targets[0]['id'], 'type': relation,
                                      'layer': 'rule-candidate', 'evidenceNodeIds': rule['evidenceNodeIds'] + targets[0]['evidenceNodeIds'], 'quote': text})
    for rule in rules:
        if rule['modality'] == 'prohibited':
            relations.append({'id': stable('relation', rule['id'], 'forbids'), 'from': rule['id'],
                              'to': rule['evidenceNodeIds'][0], 'type': 'forbids', 'layer': 'rule-candidate',
                              'evidenceNodeIds': rule['evidenceNodeIds'], 'quote': rule['sourceQuote']})
    return {'schema': 'skill-lens.document-analysis.v0.1', 'caseId': graph['caseId'], 'revision': graph['revision'],
            'units': units, 'rules': rules, 'relations': relations, 'gaps': gaps,
            'boundary': 'Source classification candidates; semantic interpretation and runtime enforcement require separate evidence.'}
