"""Question-driven static investigation followed by bounded historical trace lookup."""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from pathlib import Path

from document_rules import analyze, stable
from historical_trace import search as search_traces, sanitize
from instruction_analysis import load_analysis
from validate_lens_bundle import validate as validate_bundle
from document_section_context import section_requests

FACETS = {
    'identity': ('这个 Skill 想解决什么问题？', {'identity', 'instruction'}),
    'trigger': ('什么输入或条件会触发它？', {'trigger', 'input', 'condition'}),
    'audience': ('它为谁组织表达？', {'audience'}),
    'expression': ('哪些规则决定措辞、深度和形式？', {'instruction', 'constraint', 'audience'}),
    'output': ('原文规定了什么输出？', {'output-contract'}),
    'tools': ('哪些工具、脚本或注册关系参与？', {'tool-policy'}),
    'guards': ('有哪些条件、禁止项和例外？', {'condition', 'prohibition', 'warning'}),
    'structure': ('哪些源码组件、入口或配置承担这项能力？', {'identity', 'tool-policy', 'instruction', 'output-contract'}),
    'ownership': ('这项行为由哪一层或哪个组件负责？', {'tool-policy', 'output-contract', 'instruction', 'condition'}),
    'boundary': ('哪些部分能从当前材料确认，哪些仍在边界外？', {'warning', 'condition', 'prohibition', 'tool-policy', 'output-contract'}),
}
ROLE_LABELS = {'identity': '身份与目的', 'trigger': '触发与输入', 'audience': '受众',
               'expression': '表达规则', 'output': '输出契约', 'tools': '工具与脚本', 'guards': '条件与例外',
               'structure': '源码结构', 'ownership': '职责归属', 'boundary': '边界与未知'}
RUNTIME_QUESTION = re.compile(r'这次|本次|上次|之前那次|实际|已经|没有调用|没调用|失败|报错|\b(?:this run|last run|actually|did it|why did|didn.t|failed|error)\b', re.I)


def question_plan(question):
    required = []
    if re.search(r'HTML|artifact|format|output|输出|生成|格式|文件', question, re.I): required.append('output')
    if re.search(r'audience|reader|explain|understand|novice|manager|受众|角色|看懂|零基础|表达|讲|解释|经理', question, re.I): required += ['audience', 'expression']
    if re.search(r'tool|hook|mcp|script|call|调用|工具|脚本|执行|链路|注册', question, re.I): required.append('tools')
    if re.search(r'component|file|asset|reference|package|entrypoint|command|结构|文件|组件|资源|入口|命令|包|启动|注册', question, re.I): required.append('structure')
    if re.search(r'ownership|owner|belong|host|layer|boundary|归属|负责|属于|宿主|边界|黑盒|未知', question, re.I): required.append('ownership')
    if re.search(r'unknown|unverified|remain|outside|边界|未知|无法确认|不能从', question, re.I): required.append('boundary')
    if re.search(r'trigger|input|触发|参数|输入|启用', question, re.I): required.append('trigger')
    if not required:
        if re.search(r'why|how|mechanism|principle|为什么|如何|原理|机制|看懂|解释', question, re.I):
            required = ['audience', 'expression', 'output']
        elif re.search(r'unknown|boundary|black.?box|remain|outside|未知|边界|黑盒|无法确认|仍然', question, re.I):
            required = ['boundary']
        elif re.search(r'what|which|where|who|when|什么|哪些|哪个|哪里|谁|何时', question, re.I):
            required = ['structure']
        else:
            required = ['identity', 'structure']
    return list(dict.fromkeys(required)), bool(RUNTIME_QUESTION.search(question))


def _rank(unit, question, roles):
    text = unit['quote'].casefold()
    tokens = re.findall(r'[a-z0-9_-]{3,}', question.casefold())
    score = 10 * len(set(unit['roles']) & roles) + 12 * sum(t in text for t in tokens)
    if 'SKILL.md' in unit['path']: score += 8
    if unit['blockType'] == 'heading_open': score -= 8
    if unit['blockType'] in ('bullet_list_open', 'ordered_list_open'): score -= 5
    if unit['blockType'] == 'frontmatter': score += 4
    if 'example' in unit['roles']: score -= 100
    return score


def investigate(graph, document, question, max_depth=4, max_nodes=120, max_chars=24000):
    """References and source graph edges recurse. No model thoughts are claimed."""
    required, runtime = question_plan(question)
    nodes = {n['id']: n for n in graph['nodes']}
    units = {u['nodeId']: u for u in document['units']}
    refs = {e['id']: e for e in graph['evidence']}
    source_nodes = {n['id'] for n in graph['nodes'] if n.get('attributes', {}).get('kind') == 'markdown-span'}
    relations = {}
    # Resolve document references first so a question can enter a referenced
    # Markdown file/section even when the anchor text itself is generic.
    for link in document['relations']:
        if link['type'] == 'references': relations.setdefault(link['from'], []).append(link['to'])
    for edge in graph['edges']:
        # Containment isn't an execution chain. Recurse only existing precise
        # source links and call candidates, retaining their original type.
        if edge['type'] not in ('contains', 'declares', 'has-coverage-gap'):
            relations.setdefault(edge['from'], []).append(edge['to'])
    # A question about a handler, registration, package entrypoint, or
    # ownership needs structural facts even when no prose role matches. Keep
    # direct source nodes and explicit graph neighbors as evidence; these are
    # source relationships, never inferred execution edges.
    structural_question = bool(re.search(r'entrypoint|package|handler|registration|hook|plugin|ownership|owner|host|component|file|asset|boundary|runtime|入口|包|处理器|注册|钩子|插件|归属|宿主|组件|文件|边界|运行', question, re.I))
    structural_ids = []
    if structural_question:
        structural_ids = [n['id'] for n in graph['nodes'] if n['id'] not in source_nodes and n.get('type') in ('Artifact', 'Component', 'Scenario', 'Capability', 'Uncertainty', 'Limitation')]
    question_terms = set(re.findall(r'[a-z][a-z0-9_-]{2,}', question.casefold()))

    def structural_rank(node):
        text = ' '.join([str(node.get('label', '')), json.dumps(node.get('attributes', {}), ensure_ascii=False)]).casefold()
        score = sum(3 for term in question_terms if term in text)
        if any(word in question.casefold() for word in ('entrypoint', 'package', 'command', '启动', '入口', '包')):
            score += 12 if ('index.ts' in text or 'package.json' in text or 'bin' in text or 'command' in text) else 0
        if any(word in question.casefold() for word in ('transport', '传输')):
            score += 10 if 'transport' in text else 0
        if any(word in question.casefold() for word in ('factory', 'construct', 'create', '创建')):
            score += 10 if 'create' in text or 'factory' in text else 0
        if any(word in question.casefold() for word in ('registration', 'register', '注册')):
            score += 10 if 'register' in text or 'registration' in text else 0
        if any(word in question.casefold() for word in ('tool', '工具')):
            score += 10 if 'tool' in text else 0
        return score

    structural_ids.sort(key=lambda nid: (-structural_rank(nodes[nid]), nid))
    ref_targets = {target for link in document['relations'] if link['type'] == 'references'
                   for target in (link.get('to'),)}
    selected, tree, stopped, total_chars = {}, [], [], 0
    facets = list(dict.fromkeys([*required, 'trigger', 'guards']))
    omitted = []
    # Build a compact primary slice, then enrich it with existing Markdown
    # section/step groups. This keeps qualifiers and sibling steps together
    # without making every heading's entire body a candidate.
    primary = {'selected': []}
    for u in units.values():
        if any(word in u['quote'].casefold() for word in question_terms):
            primary['selected'].append({'nodeId': u['nodeId'], 'evidenceId': u['evidenceId']})
    q_lower = question.casefold()
    preferred_terms = set()
    if re.search(r'test|eval|测试|评估', q_lower):
        preferred_terms |= {'eval', 'evals', 'test', 'tests', 'grading', 'grade', 'assertion', 'run'}
    if re.search(r'run|pair|baseline|运行|对照', q_lower):
        preferred_terms |= {'run', 'runs', 'baseline', 'without_skill', 'old_skill', 'subagent'}
    if re.search(r'output|path|结果|输出|目录', q_lower):
        preferred_terms |= {'output', 'outputs', 'path', 'grading', 'artifact'}
    context_pairs, context_gaps = section_requests(
        graph, primary, question,
        lambda text: re.findall(r'[a-z][a-z0-9_-]{2,}', text.casefold()),
        max_groups=2, preferred_terms=preferred_terms)
    # Explanation reports have a smaller, user-facing context budget than the
    # generic slice protocol. Prioritize complete spans from the first-ranked
    # section groups while leaving the generic slicer round-robin contract
    # unchanged for projector handoffs.
    context_pairs = list(dict.fromkeys(context_pairs))
    # Concrete workflow questions need their named comparison/output blocks,
    # which lexical section ranking can otherwise place behind generic prose.
    if preferred_terms:
        refs_by_id = {ref['id']: ref for ref in graph.get('evidence', [])}
        def preference(pair):
            quote = refs_by_id.get(pair[1], {}).get('quote', '').casefold()
            return -sum(term in quote for term in preferred_terms)
        context_pairs.sort(key=preference)
    context_ids = [pair[0] for pair in context_pairs]
    for facet in facets:
        role_set = FACETS[facet][1]
        candidates = [u for u in units.values() if set(u['roles']) & role_set and 'example' not in u['roles']]
        candidates.sort(key=lambda u: (-_rank(u, question, role_set), u['path'], u['start']))
        initial = candidates[:8]
        # For question-specific mechanism facets, include high-signal source
        # spans even when their lexical role classifier is unknown. This is
        # common for numbered Markdown workflows and headings whose meaning is
        # supplied by the surrounding text.
        if facet in ('structure', 'expression', 'output', 'audience'):
            lexical = [u for u in units.values() if any(word in u['quote'].casefold() for word in re.findall(r'[a-z][a-z0-9_-]{2,}', question.casefold()))]
            lexical.sort(key=lambda u: (-_rank(u, question, role_set), u['path'], u['start']))
            seen_initial = set()
            initial = [u for u in initial + lexical[:8] if not (u['nodeId'] in seen_initial or seen_initial.add(u['nodeId']))]
        if facet == 'tools':
            initial_ids = [u['nodeId'] for u in initial]
            initial_ids += [n['id'] for n in nodes.values() if n.get('attributes', {}).get('event') or n.get('attributes', {}).get('kind') in ('call', 'hook-handler', 'mcp-registration')][:8]
            initial_ids += structural_ids[:12]
            initial_ids += context_ids[:24]
        elif facet in ('structure', 'ownership', 'boundary'):
            initial_ids = [u['nodeId'] for u in initial] + structural_ids[:20] + context_ids[:24]
        else: initial_ids = [u['nodeId'] for u in initial]
        initial_ids += context_ids[:16]
        scope = {(u['path'], tuple(u['headingPath'])) for u in initial}
        # When a question names a concept that appears in a linked reference,
        # retain the exact nearby body and its heading siblings. This is
        # bounded source context, not a new cross-file execution relation.
        question_words = [w for w in re.findall(r'[a-z][a-z0-9_-]{2,}', question.casefold())]
        for unit in units.values():
            quote_lower = unit['quote'].casefold()
            if question_words and any(word in quote_lower for word in question_words):
                scope.add((unit['path'], tuple(unit['headingPath'])))
        contextual = [u for u in units.values() if (u['path'], tuple(u['headingPath'])) in scope
                      and u['nodeId'] not in initial_ids and 'example' not in u['roles']]
        contextual.sort(key=lambda u: (-sum(word in u['quote'].casefold() for word in question_words),
                                       -len(u['headingPath']), u['path'], u['start']))
        initial_ids += [u['nodeId'] for u in contextual[:8]]
        # Bring referenced targets into the same bounded queue only when their
        # anchor or target contains a question term; this avoids importing an
        # entire reference tree for every question.
        referenced = []
        for target in ref_targets:
            unit = units.get(target)
            if unit and (any(word in unit['quote'].casefold() for word in question_words)
                         or any(word in ' '.join(unit['headingPath']).casefold() for word in question_words)):
                referenced.append(unit)
        referenced.sort(key=lambda u: (-_rank(u, question, role_set), u['path'], u['start']))
        initial_ids += [u['nodeId'] for u in referenced[:12]]
        # Bring enclosing instructions and exceptions back before resolving.
        context = [u['nodeId'] for u in units.values() if any(u['path'] == path and u['headingPath'][:len(heading)] == list(heading) for path, heading in scope if heading) and 'example' not in u['roles']]
        queue = deque((nid, 1, None, []) for nid in dict.fromkeys(initial_ids + context))
        facet_tree = {'id': f'question:{facet}', 'question': FACETS[facet][0], 'facet': facet,
                      'children': [], 'evidenceNodeIds': [], 'status': 'unknown', 'stopReasons': []}
        visited = set()
        while queue:
            nid, depth, parent, ancestry = queue.popleft()
            if nid in ancestry:
                facet_tree['stopReasons'].append('cycle'); continue
            if nid in visited: continue
            visited.add(nid)
            if depth > max_depth:
                facet_tree['stopReasons'].append('depth-budget'); omitted.append(nid); continue
            n = nodes.get(nid)
            if not n: continue
            quote = units[nid]['quote'] if nid in units else '\n'.join(refs[eid].get('quote', '') for eid in n.get('evidence', []) if eid in refs and refs[eid].get('sourceType') in ('doc', 'code'))
            if not quote: continue
            if nid not in selected:
                if len(selected) >= max_nodes or total_chars + len(quote) > max_chars:
                    facet_tree['stopReasons'].append('source-budget'); omitted.append(nid); continue
                selected[nid] = {'nodeId': nid, 'sourceQuote': quote,
                                 'evidenceIds': [e for e in n.get('evidence', []) if e in refs and refs[e].get('sourceType') in ('doc', 'code')],
                                 'evidenceType': 'source-declared' if nid in units else 'source-structural'}
                total_chars += len(quote)
            facet_tree['evidenceNodeIds'].append(nid)
            facet_tree['children'].append({'id': stable('question-node', facet, nid), 'nodeId': nid,
                                           'parentNodeId': parent, 'depth': depth, 'stopReason': 'source-found',
                                           'relationLayer': 'source-reference' if parent else 'question-selection'})
            for target in relations.get(nid, []): queue.append((target, depth + 1, nid, [*ancestry, nid]))
        facet_tree['stopReasons'] = sorted(set(facet_tree['stopReasons']))
        facet_tree['status'] = 'static-partial' if facet_tree['stopReasons'] else 'static-sufficient' if facet_tree['evidenceNodeIds'] else 'unknown'
        tree.append(facet_tree); stopped += facet_tree['stopReasons']
    # Heading-only evidence can't satisfy a behavior question.
    missing = []
    for facet in required:
        item = next(t for t in tree if t['facet'] == facet)
        usable = [nid for nid in item['evidenceNodeIds'] if nid not in units or units[nid]['blockType'] != 'heading_open']
        # A structurally relevant source claim can satisfy a non-audience
        # question even when the lexical role classifier has no matching role.
        if facet in ('structure', 'ownership', 'boundary'):
            usable = [nid for nid in item['evidenceNodeIds'] if nid in nodes and nodes[nid].get('type') in ('Component', 'Capability', 'Scenario')]
        if not usable or item['stopReasons']: missing.append(facet)
    relevant_gaps = [g for g in document['gaps'] if g.get('nodeId') in selected or g.get('nodeId') is None]
    blocking_gaps = [g for g in relevant_gaps if g.get('reason') not in ('external-reference',)]
    if blocking_gaps: missing.append('referenced-source')
    # Coverage is necessary but not sufficient: a question that asks for
    # mechanism/causality must also have actionable rules, not just headings
    # or role labels. This keeps deterministic retrieval from overstating a
    # semantic answer.
    actionable = sum(1 for item in selected.values() if any(
        role in ('instruction', 'constraint', 'condition', 'prohibition', 'output-contract')
        for role in units.get(item['nodeId'], {}).get('roles', [])
    ))
    semantic_words = bool(re.search(r'为什么|原理|机制|如何实现|核心|why|mechanism|principle|how does', question, re.I))
    if semantic_words and actionable < 2:
        missing.append('actionable-rules')
    # Large syntax graphs need a relevance floor. A bounded slice full of
    # generic call nodes is not evidence that the requested component was
    # identified; require at least one question-term-bearing structural node.
    if structural_question and not any(structural_rank(nodes[item['nodeId']]) > 0
                                       for item in selected.values() if item['nodeId'] in nodes):
        missing.append('question-relevant-structure')
    sufficiency = {'status': 'runtime-required' if runtime else 'static-partial' if missing else 'static-sufficient',
                   'basis': 'question-facet source coverage; not an automatic semantic correctness verdict',
                   'questionScope': 'specific-run' if runtime else 'design-intent', 'missingFacets': missing,
                   'missingEvidence': ['historical-run-events', 'skill-version-binding'] if runtime else ['source:' + m for m in missing],
                   'reason': '问题要求解释一次实际运行，源码仅能提供预期规则。' if runtime else '所需规则已定位；结论范围为设计声明。' if not missing else '部分问题缺少完整源码或语义范围。',
                   'traceNeeded': runtime or bool(missing),
                   'actionableRuleCount': actionable}
    return {'questionTree': tree, 'selected': list(selected.values()), 'sufficiency': sufficiency,
            'budget': {'maxDepth': max_depth, 'maxNodes': max_nodes, 'maxChars': max_chars,
                       'usedNodes': len(selected), 'usedChars': total_chars, 'stopReasons': sorted(set(stopped)),
            'omittedNodeIds': list(dict.fromkeys(omitted))}, 'referenceGaps': relevant_gaps,
            'contextGaps': context_gaps}


def aliases_for(graph, document):
    aliases = {graph['caseId']}
    for u in document['units']:
        if 'identity' in u['roles']:
            m = re.search(r'^name:\s*[\"\']?([\w-]+)', u['quote'], re.M)
            if m: aliases.add(m[1])
    return sorted(aliases)


def build_mechanism_steps(document, investigation, claims, question):
    """Create a conservative user-facing chain from source-backed facets.

    This is a reading model, not an execution graph. Missing links stay
    explicit so a report cannot turn adjacent Markdown rules into causality.
    """
    selected = {item['nodeId'] for item in investigation['selected']}
    by_facet = {item['facet']: item for item in investigation['questionTree']}
    selected_quotes = {item['nodeId']: str(item.get('sourceQuote', '')).casefold()
                       for item in investigation['selected']}
    ordered = [('trigger', '输入与触发'), ('audience', '目标对象'),
               ('expression', '表达或决策规则'), ('tools', '工具或执行动作'),
               ('output', '输出结果'), ('boundary', '边界与例外')]
    q = question.casefold()
    # Structural questions need architecture-specific labels rather than the
    # generic trigger/tools buckets. These labels are still source-backed;
    # they only change how the selected graph nodes are grouped for reading.
    if re.search(r'mcp|传输|入口|处理器|注册|handler|transport', q):
        ordered = [('structure', '启动入口'), ('tools', '传输与服务器工厂'),
                   ('expression', '工具注册与参数规则'), ('tools', '参数校验与处理器'),
                   ('output', '处理器结果'), ('boundary', '边界与错误路径')]
    elif re.search(r'生成|编辑|保存|image|图片', q):
        ordered = [('trigger', '请求类型'), ('tools', '生成或编辑路径'),
                   ('output', '保存与交付'), ('boundary', '失败与覆盖边界')]
    elif re.search(r'创建|验证|迭代|skill', q):
        ordered = [('trigger', '需求与触发'), ('structure', 'Skill 文件组织'),
                   ('expression', '验证规则'), ('output', '验证结果与修订'), ('boundary', '未确认的迭代环节')]
    elif re.search(r'前端|模板|审美|design|brief|受众|页面', q):
        ordered = [('trigger', '需求与受众信号'), ('audience', '设计判断'),
                   ('expression', '设计参数与条件'), ('guards', '反默认规则与检查'), ('output', '页面交付'), ('boundary', '适用范围与限制')]
    elif re.search(r'hook|会话|上下文|注入|sessionstart|解释性', q):
        ordered = [('trigger', '会话启动触发'), ('expression', '注入的解释要求'),
                   ('output', '可见解释输出'), ('boundary', 'Hook 适用范围')]
    elif re.search(r'eli5|看懂|零基础|角色|受众|简单|大图|少字', q):
        ordered = [('trigger', '触发与主题'), ('audience', '零基础受众'),
                   ('expression', '表达压缩规则'), ('output', 'HTML 图文交付'),
                   ('boundary', '未确认的易懂性检查')]
    steps = []
    for facet, title in ordered:
        tree = by_facet.get(facet)
        # A mechanism step should point to a small, inspectable evidence set.
        # Prefer source spans whose text names the stage itself; the full
        # question slice remains available in the evidence section.
        candidates = [nid for nid in (tree or {}).get('evidenceNodeIds', []) if nid in selected]
        stage_terms = {
            '启动入口': ('entry', 'start', 'bin', 'command', 'run'),
            '传输与服务器工厂': ('transport', 'stdio', 'sse', 'streamable', 'factory', 'server'),
            '工具注册与参数规则': ('register', 'tool', 'schema', 'input'),
            '参数校验与处理器': ('handler', 'schema', 'parse', 'input', 'error'),
            '处理器结果': ('handler', 'result', 'response', 'return', 'progress'),
            '生成或编辑路径': ('generate', 'edit', 'image_gen', 'cli'),
            '请求类型': ('generate', 'edit', 'new image', 'existing image', 'fallback', 'built-in'),
            '保存与交付': ('save', 'move', 'copy', 'destination', 'output'),
            'Skill 文件组织': ('skill.md', 'references', 'scripts', 'assets'),
            '验证规则': ('validate', 'test', 'check', 'lint', 'verify'),
            '需求与触发': ('example', 'request', 'requirement', 'need', 'trigger', 'use when'),
            '验证结果与修订': ('validate', 'validation', 'quick_validate', 'revise', 'iterate', 'feedback', 'fix'),
            '输出结果': ('output', 'artifact', 'html', 'file'),
            '需求与受众信号': ('page kind', 'vibe words', 'reference signals', 'audience', 'brand assets', 'quiet constraints'),
            '设计判断': ('design read', 'vibe', 'aesthetic', 'page kind', 'audience'),
            '设计参数与条件': ('dial', 'design_variance', 'motion_intensity', 'density', 'when'),
            '反默认规则与检查': ('anti-default', 'slop', 'pre-flight', 'check', 'prohibition'),
            '页面交付': ('component', 'layout', 'color', 'motion', 'output'),
            '会话启动触发': ('sessionstart', 'session start', 'hook', 'startup'),
            '注入的解释要求': ('explain', 'explanation', 'context', 'trade-off', 'codebase'),
            '可见解释输出': ('formatted', 'explain', 'output', 'before', 'after'),
            '触发与主题': ('use when', '/eli5', 'topic', 'trigger'),
            '零基础受众': ('knows nothing', "like i'm 5", 'audience', 'five year'),
            '表达压缩规则': ('html artifact', 'big pictures', 'few words', 'very few words'),
            'HTML 图文交付': ('produces a html artifact', 'html artifact', 'pictures', 'output'),
        }.get(title, ())
        # Add source nodes from other facets when their actual quote names the
        # requested stage. This is essential for structural Skills where the
        # architecture docs and syntax nodes carry the mechanism evidence.
        for nid, quote in selected_quotes.items():
            if nid not in candidates and stage_terms and any(term in quote for term in stage_terms):
                candidates.append(nid)
        candidates.sort(key=lambda nid: (-sum(term in selected_quotes.get(nid, '') for term in stage_terms), nid))
        direct_candidates = [nid for nid in candidates if stage_terms and any(term in selected_quotes.get(nid, '') for term in stage_terms)]
        # A stage citation must name the stage itself. Neighboring rules remain
        # available in the evidence drawer, but cannot be promoted as direct
        # support for a different phase.
        ids = (direct_candidates[:3] if direct_candidates else candidates[:3])
        if facet == 'structure' and not ids:
            ids = [item['nodeId'] for item in investigation['selected'] if item['nodeId'] in selected][:3]
        if not ids and facet not in ('boundary',):
            continue
        direct_matches = sum(1 for nid in ids if any(term in selected_quotes.get(nid, '') for term in stage_terms))
        # A stage is source-supported only when at least half of its cited
        # nodes explicitly name the stage. One lexical hit among unrelated
        # nodes is an interpretation link, not direct proof of the whole step.
        status = 'source-supported' if ids and direct_matches >= max(1, (len(ids) + 1) // 2) else 'inferred-link' if ids else 'unconfirmed'
        if facet == 'structure' and ('MCP' in title or '启动' in title):
            input_text, decision, action, observable = ('启动命令或服务入口', '选择哪个通信入口承接客户端连接', '进入对应 server factory 或入口模块', '源码中能定位入口、工厂和传输适配器')
        elif facet == 'tools' and '传输' in title:
            input_text, decision, action, observable = ('客户端连接方式', '选择 STDIO、SSE 或 Streamable HTTP', '创建或连接 MCP server', '能看到传输适配器把连接交给同一服务器实例')
        elif facet == 'expression' and '注册' in title:
            input_text, decision, action, observable = ('server 实例与工具目录', '按名称和参数 schema 绑定哪个工具', '调用 registerTools 或具体注册函数', '工具名称、输入校验和 handler 绑定可定位')
        elif facet == 'tools' and '参数校验' in title:
            input_text, decision, action, observable = ('工具名称与请求参数', '检查 schema 是否允许这次调用', '交给对应 handler 执行并处理错误', '能看到参数校验、成功结果或错误返回')
        elif facet == 'output' and '处理器' in title:
            input_text, decision, action, observable = ('客户端工具名与参数', '选择对应 handler 并校验输入', '执行处理器并封装 MCP 响应', '返回结果、错误或进度事件可被观察')
        elif facet == 'guards' and '反默认' in title:
            input_text, decision, action, observable = ('设计判断和适用条件', '是否启用某个视觉模式，哪些默认套路必须排除', '应用条件规则并执行交付前检查', '输出中能看到被选择的模式、排除项和检查结果')
        elif facet == 'audience' and '设计判断' in title:
            input_text, decision, action, observable = ('brief、页面类型、受众和品牌资产', '是否需要澄清，以及采用什么设计方向', '先输出一行 Design Read', '代码前能看到页面类型、受众、氛围和技术倾向')
        elif facet == 'trigger' and '需求与受众' in title:
            input_text, decision, action, observable = ('用户 brief、页面类型、受众和已有品牌资产', '哪些约束优先，是否需要补问', '读取场景信号并确定设计问题边界', 'Design Read 或分析记录包含这些输入')
        elif facet == 'expression' and '设计参数' in title:
            input_text, decision, action, observable = ('Design Read 的判断', '把判断转换为布局、颜色、动效和密度参数', '用全局参数驱动后续组件选择', '同一组参数出现在后续设计规则中')
        elif facet == 'trigger' and '需求与触发' in title:
            input_text, decision, action, observable = ('要解决的具体问题和示例', '确定 Skill 的用途、触发条件和需要复用的知识', '先整理需求，再决定是否创建或修改 Skill', '源码给出用途、触发描述或示例；不证明当前任务已触发')
        elif facet == 'structure' and 'Skill 文件组织' in title:
            input_text, decision, action, observable = ('可复用的知识、流程和资源', '哪些内容放进 SKILL.md，哪些拆到 scripts、references 或 assets', '组织 Skill 文件和渐进式披露层级', '源码能定位必需文件、可选目录和引用关系；不证明文件已创建')
        elif facet == 'expression' and '验证规则' in title:
            input_text, decision, action, observable = ('已组织的 Skill 文件', '是否满足格式、结构和内容检查', '运行或遵循 quick_validate、测试和审查规则', '源码能定位验证命令或检查条件；当前报告没有证明命令已运行')
        elif facet == 'output' and '验证结果与修订' in title:
            input_text, decision, action, observable = ('验证发现的问题和真实使用反馈', '是否需要修改后再次检查', '修订 Skill 并重新验证', '源码描述迭代闭环，但当前没有失败记录或再次通过的运行证据')
        elif facet == 'trigger' and '请求类型' in title:
            input_text, decision, action, observable = ('提示词、已有图片和用户对工具的要求', '区分新图生成、已有图编辑，以及是否明确要求 CLI', '确定后续的生成/编辑分支和执行器', '源码规则包含 generate、edit 和 explicit CLI 条件；不证明本次请求走了哪条分支')
        elif facet == 'tools' and '生成或编辑路径' in title:
            input_text, decision, action, observable = ('用户的生成或编辑请求', '选择内置 image_gen，或只有在用户明确要求时选择 CLI', '进入生成或编辑执行路径', '源码明确区分 built-in 与 CLI fallback；不证明工具已被调用')
        elif facet == 'output' and '保存与交付' in title:
            input_text, decision, action, observable = ('生成器返回的图片文件', '是否遵守内置工具的默认目录和项目资产规则', '把选定输出保存或移动到目标位置', '源码规定保存路径和不可静默覆盖；当前没有生成文件的运行证据')
        elif facet == 'output' and '页面交付' in title:
            input_text, decision, action, observable = ('已选设计方向和参数', '是否满足依赖与 pre-flight 检查', '生成页面并交付可运行实现', '出现页面代码和检查结果；实际质量仍未证明')
        elif facet == 'trigger' and '会话启动' in title:
            input_text, decision, action, observable = ('插件已启用且会话开始', '是否触发 SessionStart hook', '注入额外解释性上下文', '会话开始事件和注入内容可在运行记录中观察')
        elif facet == 'expression' and '注入' in title:
            input_text, decision, action, observable = ('注入的解释性上下文', '要求解释哪些代码选择、模式和权衡', '把说明要求加入后续会话上下文', '后续回答出现规定的解释结构')
        elif facet == 'output' and '解释输出' in title:
            input_text, decision, action, observable = ('代码变更和上下文', '哪些内容值得向用户解释', '输出简短的实现说明和权衡', '用户看到解释性段落；静态报告不能证明实际输出')
        elif facet == 'trigger' and '触发与主题' in title:
            input_text, decision, action, observable = ('用户输入的命令或主题', '是否匹配 /eli5 <topic> 或极简图解请求', '把主题交给该 Skill 的解释规则', '源码中能定位触发描述和 Topic 参数；没有运行记录时不代表已触发')
        elif facet == 'audience' and '零基础受众' in title:
            input_text, decision, action, observable = ('待解释的主题与目标读者', '把读者视为不了解该主题的人', '降低术语和背景要求，按初学者组织说明', '源码明确写出 knows nothing/about five；易懂程度仍需输出评估')
        elif facet == 'expression' and '表达压缩规则' in title:
            input_text, decision, action, observable = ('零基础受众设定', '采用少文字、大图和 HTML 载体', '压缩文字并用图文结构解释主题', '源码要求 big pictures、few words 和 HTML artifact；不是已生成的页面')
        elif facet == 'output' and 'HTML 图文交付' in title:
            input_text, decision, action, observable = ('主题和表达规则', '是否满足 HTML、图像和简短文字的交付约束', '生成 HTML 图文解释物', '源码声明会产生 HTML artifact；渲染器、图片和质量检查仍未知')
        elif facet == 'trigger':
            input_text, decision, action, observable = ('用户请求、输入文件或上下文', 'Skill 是否被触发以及需要哪些输入', '读取并整理触发条件', '报告中能看到触发词、输入类型或前置条件')
        elif facet == 'audience':
            input_text, decision, action, observable = ('已识别的用户或受众', '表达深度和语气是否需要调整', '选择面向该受众的表达方式', '报告或输出中出现受众、角色或理解水平约束')
        elif facet == 'expression':
            input_text, decision, action, observable = ('触发条件与受众判断', '哪些规则决定措辞、结构或技术选择', '应用规则、限制和例外', '输出中出现对应格式、禁止项或条件分支')
        elif facet == 'tools':
            input_text, decision, action, observable = ('规则要求的工具、脚本或入口', '选择哪条工具路径', '调用或注册对应组件', '能在源码或历史记录中看到工具入口和调用结果')
        elif facet == 'output':
            input_text, decision, action, observable = ('前面规则产生的中间结果', '满足什么条件才算交付', '生成、保存或返回规定格式', '出现规定的文件、响应或交付检查')
        elif facet == 'boundary' and title == '未确认的易懂性检查':
            input_text, decision, action, observable = ('源码中的受众与格式要求', '是否真正让目标读者看懂', '需要运行输出或读者反馈来验证', '当前只有声明，没有易懂性评估或运行结果')
        else:
            input_text, decision, action, observable = ('已知规则与证据范围', '哪些行为仍不能从当前材料确认', '把未知和例外单独标记', '报告显示未确认项，而不是补写推断')
        steps.append({'id': stable('mechanism-step', facet, question), 'title': title,
                      'input': input_text, 'decision': decision, 'action': action,
                      'observable': observable, 'evidenceNodeIds': ids,
                      'status': status, 'boundary': '该步骤描述源码声明或阅读模型；不等于已观察到运行时执行。' if ids else '当前选定证据不足，不能确认这一环节。'})
    return steps


def build_decision_rules(mechanism_steps):
    """Expose the reusable decision layer behind every Skill report.

    A Skill may publish named presets, conditional branches, or only a
    sequential workflow.  This normalizes all three into the same compact
    shape without pretending that adjacent Markdown paragraphs are runtime
    causality.
    """
    rules = []
    for step in mechanism_steps:
        if step.get('status') == 'unconfirmed' and not step.get('evidenceNodeIds'):
            continue
        decision = str(step.get('decision', '')).strip()
        action = str(step.get('action', '')).strip()
        if not decision or not action:
            continue
        rules.append({
            'id': step['id'],
            'title': step['title'],
            'scenario': step.get('input', ''),
            'decision': decision,
            'action': action,
            'status': step.get('status', 'unconfirmed'),
            'evidenceNodeIds': list(step.get('evidenceNodeIds', [])),
        })
    return rules


def _claim_interpretation(facet, rule):
    """Turn a selected source rule into a distinct, short reading aid."""
    text = rule.get('sourceQuote', '').casefold()
    if facet == 'trigger':
        return '这条规则说明何时进入 Skill，以及主题或参数从哪里来。它不证明这次请求已经触发。'
    if facet == 'audience':
        return '这条规则定义目标读者的知识起点；它是表达策略的约束，不是读者理解结果。'
    if facet == 'expression':
        return '这条规则把受众要求转换成写法或呈现形式；具体执行仍要看生成的输出。'
    if facet == 'output':
        return '这条规则声明交付物的形式；它不证明渲染器、图片或质量检查已经成功运行。'
    if facet == 'tools':
        return '这条规则指出允许使用的工具或脚本；工具是否实际被调用要看运行记录。'
    if facet == 'guards':
        return '这条规则给出条件、禁止项或例外，用来限制前面的路径。'
    if facet == 'structure':
        return '这是源码结构事实，用来定位职责归属，不等于执行顺序。'
    if 'must not' in text or 'never' in text:
        return '这条规则表达限制条件；其存在不代表运行时一定遵守。'
    return '这条源码规则与当前问题相关；它的适用范围以原文条件为准。'


def _summary_for_question(question, claims):
    q = question.casefold()
    if re.search(r'eli5|看懂|零基础|角色|受众|大图|少字', q):
        return '它先把读者设定为不了解主题的人，再把表达压缩成少文字、配图的 HTML 图文说明。'
    if re.search(r'创建|验证|迭代|skill', q):
        return '它把创建 Skill 拆成需求、文件组织、验证和修订几个阶段。'
    if re.search(r'生成|编辑|保存|image|图片', q):
        return '它先根据请求选择生成或编辑，再按工具路径处理并保存结果。'
    if re.search(r'mcp|传输|入口|处理器|注册|handler|transport', q):
        return '它把 MCP 请求串成入口、传输、服务器工厂、工具注册、参数校验和处理器结果。'
    if re.search(r'前端|模板|审美|design|brief|受众|页面', q):
        return '它先读需求和受众，再确定设计方向、参数和反默认检查，最后交付页面。'
    if re.search(r'hook|会话|上下文|注入|sessionstart', q):
        return '会话启动时，Hook 按规则加入额外解释性上下文，影响后续回答的组织方式。'
    if claims:
        return '报告按问题筛选源码规则，并把能确认的声明与仍未知的部分分开。'
    return '当前材料无法形成有出处的机制结论，需要补足源码覆盖或相关执行证据。'


def build(bundle, question, static_only=False, trace_roots=None, session_id=None,
          max_depth=4, max_nodes=120, max_chars=24000, max_traces=5, time_window_days=None):
    bundle = Path(bundle)
    structural_question = bool(re.search(r'entrypoint|package|handler|registration|hook|plugin|ownership|owner|host|component|file|asset|boundary|runtime|入口|包|处理器|注册|钩子|插件|归属|宿主|组件|文件|边界|运行', question, re.I))
    if not question.strip(): raise ValueError('question must not be empty')
    if min(max_depth, max_nodes, max_chars, max_traces) <= 0: raise ValueError('budgets must be positive')
    baseline = validate_bundle(bundle)
    if not baseline['valid']: raise ValueError('invalid Bundle: ' + '; '.join(baseline['errors']))
    raw = (bundle / 'evidence-graph.json').read_bytes()
    graph = json.loads(raw)
    document = analyze(graph)
    if not document['units']:
        document['gaps'].append({'nodeId': None, 'reason': 'no-markdown-source-spans',
                                 'target': 'markdown-sections Provider required for instruction explanation'})
    investigation = investigate(graph, document, question, max_depth, max_nodes, max_chars)
    analysis = load_analysis(bundle)
    selected_ids = {e['nodeId'] for e in investigation['selected']}
    nodes_by_id = {node['id']: node for node in graph['nodes']}
    prefer_structural_claims = structural_question and any(
        nodes_by_id.get(nid, {}).get('type') in ('Component', 'Capability', 'Scenario')
        and any(ref.get('sourceType') == 'code' for ref in graph['evidence']
                if ref['id'] in nodes_by_id.get(nid, {}).get('evidence', []))
        for nid in selected_ids
    )
    claims = []
    if analysis:
        for stage in analysis['stages']:
            if set(stage['evidenceNodeIds']).issubset(selected_ids):
                claims.append({'id': stable('claim', stage['id']), 'label': stage['label'], 'text': stage['instruction'],
                               'interpretation': stage['interpretation'], 'evidenceType': 'analyst-interpreted',
                               'evidenceNodeIds': stage['evidenceNodeIds'], 'traceEventRefs': [], 'verification': 'citation-gate-passed'})
    if not claims and not prefer_structural_claims:
        rule_map = {nid: [] for nid in selected_ids}
        for rule in document['rules']:
            for nid in rule['evidenceNodeIds']:
                if nid in rule_map: rule_map[nid].append(rule)
        used_claim_nodes = set()
        facet_terms = {
            'trigger': ('use when', 'trigger', '/eli5', 'arguments', 'topic', 'when enabled'),
            'audience': ('knows nothing', "like i'm 5", 'audience', 'reader', 'novice', 'beginner'),
            'expression': ('html', 'artifact', 'big pictures', 'few words', 'format', 'style', 'structure'),
            'output': ('produces', 'output', 'artifact', 'return', 'deliver', 'html'),
            'tools': ('tool', 'script', 'command', 'invoke', 'run', 'execute'),
            'guards': ('must not', 'never', 'only when', 'unless', 'avoid', 'if'),
            'structure': ('skill.md', 'references', 'scripts', 'assets', 'file', 'component'),
        }
        if re.search(r'hook|会话|上下文|注入|sessionstart|解释性', question, re.I):
            facet_terms['expression'] = ('sessionstart', 'hook', 'inject', 'context', 'explain', 'formatted', 'codebase')
            facet_terms['output'] = ('formatted', 'before', 'after', 'explanation', 'output')
        if re.search(r'前端|模板|审美|design|brief|受众|页面', question, re.I):
            facet_terms['trigger'] = ('page kind', 'vibe words', 'reference signals', 'audience', 'brand assets', 'quiet constraints')
            facet_terms['output'] = ('ship interfaces', 'pre-flight', 'deliver', 'page', 'component', 'build', 'output')
        for item in investigation['questionTree']:
            quoted = [r for nid in item['evidenceNodeIds'] for r in rule_map.get(nid, []) if set(r['roles']) & FACETS[item['facet']][1]]
            if not quoted and item['facet'] in ('structure', 'ownership', 'boundary'):
                quoted = [r for nid in item['evidenceNodeIds'] for r in rule_map.get(nid, [])]
            if quoted:
                terms = facet_terms.get(item['facet'], ())
                def rule_score(rule):
                    text = rule['sourceQuote'].casefold()
                    role_bonus = len(set(rule['roles']) & FACETS[item['facet']][1]) * 20
                    term_bonus = sum(term in text for term in terms) * 12
                    duplicate_penalty = 18 if any(nid in used_claim_nodes for nid in rule['evidenceNodeIds']) else 0
                    # Prefer a concrete leaf over a frontmatter/container span
                    # when both express the same concept.
                    length_bonus = min(len(text), 180) / 100
                    return role_bonus + term_bonus + length_bonus - duplicate_penalty
                rule = max(quoted, key=rule_score)
                used_claim_nodes.update(rule['evidenceNodeIds'])
                claims.append({'id': stable('claim', item['facet'], rule['id']), 'label': ROLE_LABELS[item['facet']],
                               'text': '原文规定：' + rule['sourceQuote'],
                               'interpretation': _claim_interpretation(item['facet'], rule),
                               'evidenceType': 'source-declared', 'evidenceNodeIds': rule['evidenceNodeIds'],
                               'traceEventRefs': [], 'verification': 'exact-source-quote'})
    if not claims and structural_question:
        evidence_by_id = {ref['id']: ref for ref in graph['evidence']}
        selected_nodes = [item['nodeId'] for item in investigation['selected']]
        structural_terms = ('server', 'transport', 'stdio', 'sse', 'http', 'register',
                            'handler', 'schema', 'tool', 'factory', 'echo', 'mcp')
        def structural_claim_rank(nid):
            node = nodes_by_id.get(nid, {})
            attrs = node.get('attributes', {})
            label = str(node.get('label', '')).casefold()
            refs = [evidence_by_id.get(eid, {}) for eid in node.get('evidence', [])]
            has_code = any(ref.get('sourceType') == 'code' for ref in refs)
            syntax = str(attrs.get('syntaxType', '')).casefold()
            rank = 0
            rank += 100 if has_code else 0
            rank += 25 if syntax in ('function_declaration', 'call_expression', 'variable_declaration', 'method_definition') else 0
            rank += 8 * sum(term in label for term in structural_terms)
            rank += 4 * sum(term in syntax for term in ('call', 'function', 'declaration'))
            return rank
        selected_nodes.sort(key=lambda nid: (-structural_claim_rank(nid), nid))
        seen_paths = set()
        for nid in selected_nodes:
            node = nodes_by_id.get(nid)
            if not node or node.get('type') not in ('Component', 'Capability', 'Scenario'):
                continue
            code_refs = [evidence_by_id[eid] for eid in node.get('evidence', [])
                         if eid in evidence_by_id and evidence_by_id[eid].get('sourceType') == 'code']
            if not code_refs:
                continue
            paths = {str(e.get('source', {}).get('path', '')) for e in code_refs}
            if paths & seen_paths and len(seen_paths) < 4:
                continue
            quote = '\n'.join(e.get('quote', '') for e in code_refs)
            if not quote: continue
            label = node.get('label', '源码节点')
            source_path = str(code_refs[0].get('source', {}).get('path', ''))
            snippet = re.sub(r'\s+', ' ', quote).strip()
            if len(snippet) > 180:
                snippet = snippet[:177] + '...'
            if 'register' in snippet.casefold():
                readable_label = '注册调用 · ' + source_path
            elif 'transport' in snippet.casefold() or 'stdio' in snippet.casefold() or 'sse' in snippet.casefold():
                readable_label = '传输入口 · ' + source_path
            elif 'mcpserver' in snippet.casefold() or 'new McpServer'.casefold() in snippet.casefold():
                readable_label = '服务器创建 · ' + source_path
            else:
                readable_label = str(label) + ' · ' + source_path
            claims.append({'id': stable('claim', 'structural', nid), 'label': '源码事实：' + readable_label,
                           'text': '源码片段：' + snippet,
                           'interpretation': '这是 Provider 从源码结构或配置中提取的事实；它不是运行时执行证明。',
                           'evidenceType': 'source-structural', 'evidenceNodeIds': [nid],
                           'traceEventRefs': [], 'verification': 'graph-source-evidence'})
            seen_paths.update(paths)
            if len(claims) >= 8: break
    mechanism_steps = build_mechanism_steps(document, investigation, claims, question)
    decision_rules = build_decision_rules(mechanism_steps)
    artifact = json.loads((bundle / 'artifact.json').read_text())
    manifest = json.loads((bundle / 'analysis-manifest.json').read_text())
    diagnostics_file = json.loads((bundle / 'diagnostics.json').read_text())
    source_root = artifact.get('path')
    traces = {'schema': 'skill-lens.trace-search.v0.1', 'status': 'not-requested', 'slices': [], 'index': {'schema': 'skill-lens.trace-index.v0.1', 'records': []}, 'diagnostics': [], 'searchedFileCount': 0, 'eventCount': 0,
              'selection': {'mode': 'not-requested', 'decision': 'static-evidence-sufficient'}}
    if investigation['sufficiency']['traceNeeded'] and not static_only:
        traces = search_traces(aliases_for(graph, document), graph['revision'], source_root, trace_roots,
                              session_id, max_traces, time_window_days=time_window_days)
    elif static_only:
        traces['status'] = 'disabled'
        traces['selection'] = {'mode': 'disabled', 'decision': 'static-only-requested'}
    unknowns = [
        {'question': '模型是否严格遵循了全部规则、读者是否理解？', 'reason': '源码和工具日志无法单独证明；需要输出评估或读者反馈。'},
        {'question': '模型内部为何选择这条路径？', 'reason': '仅解释可观察规则与事件；私有推理不在证据范围内。'},
    ]
    if investigation['sufficiency']['traceNeeded'] and not traces['slices']:
        unknowns.append({'question': '这次实际加载和执行了什么？', 'reason': '没有找到可关联的既有 Trace，或历史回溯被关闭。'})
    for gap in investigation['referenceGaps']:
        unknowns.append({'question': '被引用内容是否改变了规则？', 'reason': gap['reason'] + ': ' + gap['target']})
    for slice_ in traces['slices']:
        if slice_['revisionBinding'] != 'matched':
            unknowns.append({'question': '这段历史记录对应哪个 Skill 版本？', 'reason': '记录与目标版本冲突。' if slice_['revisionBinding'] == 'conflicting' else '缺少明确的 Skill 版本绑定；项目提交不能替代 Skill 版本。'})
        unknowns.append({'question': '这条记录是否覆盖完整执行？', 'reason': '当前是筛选后的事件片段；缺少事件不能证明操作没有发生。'})
    if investigation['budget']['stopReasons']:
        unknowns.append({'question': '还有哪些源码没有继续追踪？', 'reason': ', '.join(investigation['budget']['stopReasons'])})
    summary = analysis['summary'] if analysis and claims else _summary_for_question(question, claims)
    if investigation['sufficiency']['questionScope'] == 'specific-run':
        summary = '源码可解释预期行为；这次运行的原因需结合下面的历史事件判断。' + (' ' + summary if claims else '')
    comparisons = []
    source_text = ' '.join(r['sourceQuote'].casefold() for r in document['rules'])
    # Only explicit skill-loaded or artifact events can support these limited
    # observations. Text similarity never proves causation or comprehension.
    for s in traces['slices']:
        for event in s['events']:
            if event['type'] in ('skill-loaded', 'artifact', 'hook-triggered', 'error'):
                observed_text = ' '.join(str(event.get(k, '')) for k in ('name', 'skill', 'artifact', 'text')).casefold()
                overlap = sorted({token for token in re.findall(r'[a-z][a-z0-9_-]{2,}', observed_text)
                                  if token in source_text})
                comparisons.append({'traceId': s['traceId'], 'eventId': event['id'], 'status': 'observed',
                                    'observation': event.get('artifact') or event.get('skill') or event.get('name') or event.get('text', ''),
                                    'sourceMatch': 'candidate-overlap' if overlap else 'unverified',
                                    'overlapTokens': overlap,
                                    'reason': '观察到事件；词面重叠只是候选关联，不证明适用规则或因果关系。'})
    return {'schema': 'skill-lens.explanation-chain.v0.1', 'caseId': graph['caseId'], 'revision': graph['revision'],
                     'graphSha256': hashlib.sha256(raw).hexdigest(), 'question': question, 'summary': summary,
                     'status': 'trace-found' if traces['slices'] else 'static-only', 'documentAnalysis': document,
                     'investigation': investigation, 'claims': claims, 'mechanismSteps': mechanism_steps,
                     'decisionRules': decision_rules, 'traceSearch': traces, 'comparisons': comparisons,
                     'unknowns': unknowns, 'runContext': {'sourcePath': artifact.get('path'),
                       'analysisStatus': manifest.get('status'), 'revisionKind': manifest.get('revisionKind', 'unknown'),
                       'providerDiagnostics': diagnostics_file.get('diagnostics', [])},
                     'semantics': {'sourceGraph': 'static relationships', 'ruleRelations': 'source-backed candidates',
                       'traceEdges': 'explicit observed call IDs only', 'explanationOrder': 'reading order; not execution or private reasoning'}}
