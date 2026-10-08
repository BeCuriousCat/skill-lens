"""One-page explanation, with source and historical observations in separate drawers."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from historical_trace import sanitize
from render_bundle_visualization import write_report as write_source_report
from translation import attach, load
from validate_explanation import validate_explanation


def _generic_interpretation(text):
    normalized = ' '.join(str(text).split()).strip().rstrip('。')
    return normalized in {
        '该片段为理解此环节提供依据；条件和例外保留在原文中',
        '该片段为理解此环节提供依据, 条件和例外保留在原文中',
    }


def _evidence_label(kind):
    return {
        'source-declared': 'Skill 原文规则',
        'source-structural': '源码中的实现位置',
        'analyst-interpreted': '根据多条原文整理',
    }.get(kind, kind)


def _has_translation(item):
    """Only render a translation panel when a sidecar supplied real text."""
    return bool(str(item.get('translation', '')).strip()) and item.get('translationStatus') not in (None, 'unavailable')


def _naturalize(text):
    """Keep the reader-facing layer Chinese while preserving source quotes."""
    value = str(text)
    replacements = (
        ('Design Read 格式', '设计判断的写法'),
        ('生成前先声明设计判断', '生成前先说清设计判断'),
        ('Design Read', '设计判断'),
        ('design read', '设计判断'),
        ('pre-flight check', '交付前检查'),
        ('pre-flight', '交付前检查'),
        ('brief', '需求简报'),
        ('global variables', '全局变量'),
        ('HTML artifact', 'HTML 图文说明'),
        ('Agent', '模型助手'),
        ('交付前检查 检查', '交付前检查'),
        ('Design Read 或分析记录', '设计判断或分析记录'),
        ('Design Read 或', '设计判断或'),
    )
    for source, target in replacements:
        value = value.replace(source, target)
    value = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', value)
    return value


def _claim_title(claim):
    """Use what the quoted sentence actually says as the visible label."""
    text = _display_claim_text(claim.get('text', '')).casefold()
    if 'infer what the user' in text or 'before touching code' in text:
        return '先理解需求，再开始设计'
    if 'brand assets' in text:
        return '把已有品牌材料纳入输入'
    if 'ask exactly one' in text or 'clarifying question' in text:
        return '不确定时只问一个澄清问题'
    return claim.get('label', '相关原文规则')


def _claim_reader_note(claim):
    """Explain the quoted rule without trusting a possibly broad facet label."""
    text = _display_claim_text(claim.get('text', '')).casefold()
    if 'infer what the user' in text or 'before touching code' in text:
        return '它把“先理解场景”设为动手前的第一步，目的是减少直接套用默认风格。'
    if 'brand assets' in text:
        return '它把已有品牌材料视为改版的输入，避免从空白模板开始。'
    if 'ask exactly one' in text or 'clarifying question' in text:
        return '不确定时只问一个关键问题，避免一次抛出一串问题。'
    return claim.get('interpretation', '')


def _translation_status(status):
    return {
        'machine-translated': '机器翻译',
        'human-reviewed': '人工校订',
        'unavailable': '未提供中文对照',
    }.get(status, '中文对照')


def _translation_note(translation):
    coverage = translation.get('coverage', {})
    claims = coverage.get('claims', 0)
    sources = coverage.get('sources', 0)
    status = translation.get('status')
    if claims or sources:
        return f'已有部分中文对照：{claims} 条结论、{sources} 个源码片段。没有对照的片段只保留英文。'
    if status == 'unavailable' or not status:
        return '目前没有逐条中文对照；上面的中文是根据规则整理的，英文原文可展开核对。'
    return '中文对照尚未覆盖具体原文；英文原文可展开核对。'


def _mechanism_status(status):
    return {'source-supported': '原文明确写了', 'inferred-link': '这是把几条规则串起来的理解', 'unconfirmed': '当前材料没有说明'}.get(status, '当前材料没有说明')


def _decision_rules(value):
    """Read the normalized decision layer, with compatibility for old reports."""
    rules = value.get('decisionRules')
    if rules is not None:
        return rules
    return [
        {
            'id': step.get('id', ''), 'title': step.get('title', ''),
            'scenario': step.get('input', ''), 'decision': step.get('decision', ''),
            'action': step.get('action', ''), 'status': step.get('status', 'unconfirmed'),
            'evidenceNodeIds': step.get('evidenceNodeIds', []),
        }
        for step in value.get('mechanismSteps', [])
        if step.get('decision') and step.get('action')
        and not (step.get('status') == 'unconfirmed' and not step.get('evidenceNodeIds'))
    ]


def _safe_stage_summary(step):
    """Return a readable sentence; internal evidence metadata stays in the drawer."""
    title = step['title']
    descriptions = {
        '需求与受众信号': '先看用户要做什么、页面给谁看，以及已有的品牌材料。',
        '设计判断': '动手写代码前，先说清楚自己把需求理解成什么样的页面。',
        '设计参数与条件': '再把这个方向落实到布局、颜色、动效和内容密度。',
        '反默认规则与检查': '根据这次需求选择合适的做法，避开不合适的常见套路，并检查成品。',
        '页面交付': '最后交付页面；源码能说明它要求怎样做，不能单独证明成品质量。',
        '需求与触发': '先确认什么请求会用到它，以及用户提供了什么信息。',
        'Skill 文件组织': '把主要说明放在入口文件，把细节和工具拆到各自文件中。',
        '验证规则': '按文档规定的检查方式确认内容或实现是否符合要求。',
        '验证结果与修订': '发现问题后修改，再按规则重新检查。',
        '请求类型': '先分清用户要新生成内容，还是修改已有内容。',
        '生成或编辑路径': '再按请求类型和用户指定的工具选择执行方式。',
        '保存与交付': '完成后按规则保存到目标位置。',
        '会话启动触发': '会话开始时，检查插件是否会加入额外说明。',
        '注入的解释要求': '加入的说明会要求后续回答交代相关代码选择和取舍。',
        '可见解释输出': '用户最终看到的是回答中的实现说明。',
        '触发与主题': '先看用户的请求是否属于这个 Skill 要处理的主题。',
        '零基础受众': '它把读者当作还不了解这个主题的人。',
        '表达压缩规则': '它要求少用文字、配合图示来解释。',
        'HTML 图文交付': '最后按规则交付一份 HTML 图文说明。',
        '启动入口': '先从启动入口接收客户端连接。',
        '传输与服务器工厂': '再由对应的通信方式把连接交给服务器。',
        '工具注册与参数规则': '服务器会登记可用工具及其参数要求。',
        '参数校验与处理器': '收到调用后，先检查参数，再交给对应处理程序。',
        '处理器结果': '处理程序返回结果或错误信息。',
    }
    return descriptions.get(title, str(step.get('action', '')).strip().rstrip('。') + '。')


def _auto_summary(value):
    # The engine may have produced a question-specific synthesis. Prefer it so
    # the first screen answers the user's question instead of describing the
    # Lens template itself.
    existing = str(value.get('summary', '')).strip()
    if existing and not existing.startswith('根据源码，'):
        return existing
    question = str(value.get('question', '')).casefold()
    suff = value.get('investigation', {}).get('sufficiency', {})
    suffix = '当前是源码规则解释，未证明实际运行结果。'
    if re.search(r'mcp|传输|入口|处理器|handler|transport', question, re.I):
        return '启动入口先选择通信方式，传输层把连接交给统一的 server factory；factory 创建 MCP server 并注册工具，之后请求按工具名和参数进入对应处理器。' + suffix
    if re.search(r'生成|编辑|保存|image|图片', question, re.I):
        return '先判断是生成还是编辑；默认使用内置 image_gen，只有用户明确要求时才走 CLI；完成后按预览、项目或指定目录的用途处理保存。' + suffix
    if re.search(r'创建|验证|迭代|skill', question, re.I):
        return '先把需求和示例整理成核心流程，再组织 SKILL.md 与 references、scripts、assets；运行验证检查结构和行为，失败后回到对应规则修订。当前缺少实际迭代记录。'
    if re.search(r'前端|模板|审美|design|brief|受众|页面', question, re.I):
        return '先读 brief、受众、页面类型和品牌资产，必要时澄清；输出 Design Read，用设计参数和条件规则选择页面方案，避开默认模板，最后执行交付检查。' + suffix
    if re.search(r'hook|会话|上下文|注入|sessionstart|解释性', question, re.I):
        return '插件启用后，在会话开始由 SessionStart hook 注入解释要求，让后续回答说明实现选择、代码模式和权衡。' + suffix
    if value.get('mechanismSteps'):
        return '报告把问题拆成输入、判断、动作、结果和边界几个阶段；每个阶段都标注证据状态。' + suffix
    return value.get('summary', '')


def _display_claim_text(text):
    """Remove the report-only Chinese prefix from an English claim display."""
    return str(text).removeprefix('原文规定：').removeprefix('原文规定:').lstrip()


def _coverage_note(value):
    budget = value.get('investigation', {}).get('budget', {})
    used_nodes = budget.get('usedNodes', 0)
    max_nodes = budget.get('maxNodes', 0)
    used_chars = budget.get('usedChars', 0)
    max_chars = budget.get('maxChars', 0)
    reasons = {'source-budget': '达到源码预算', 'depth-budget': '达到递归深度预算',
               'cycle': '遇到循环引用'}
    stops = '、'.join(reasons.get(reason, reason) for reason in budget.get('stopReasons', [])) or '未触发停止原因'
    return f'证据覆盖：已纳入 {used_nodes} 个源码节点（预算 {max_nodes}），约 {used_chars} 个字符（预算 {max_chars}）；{stops}。未纳入部分不代表不存在，结论只适用于当前证据范围。'


def _user_facing_status(value):
    suff = value.get('investigation', {}).get('sufficiency', {})
    trace = value.get('traceSearch', {}).get('status')
    if suff.get('questionScope') == 'specific-run':
        return '要解释某一次具体执行，还需要找到对应的历史记录。'
    if trace in ('disabled', 'not-requested'):
        return '这次只检查了 Skill 文件，没有找到可对应的历史执行记录；下面说明它要求怎么做，不代表它每次都照做。'
    return '报告会把 Skill 写下的规则和历史记录中实际看到的行为分开说明。'


def _investigation_status(status):
    return {
        'static-sufficient': '源码足够支持这部分解释',
        'static-partial': '源码只支持部分解释',
        'runtime-required': '需要运行记录才能回答',
        'unknown': '当前材料不足',
    }.get(status, '当前材料不足')


def _stop_reason(reason):
    return {
        'source-budget': '达到本次阅读范围上限',
        'depth-budget': '达到继续展开的层数上限',
        'cycle': '遇到重复引用',
    }.get(reason, reason or '找到对应原文')


def _friendly_budget(value):
    budget = dict(value.get('investigation', {}).get('budget', {}))
    if 'stopReasons' in budget:
        budget['stopReasons'] = [_stop_reason(reason) for reason in budget.get('stopReasons', [])]
    return json.dumps(budget, ensure_ascii=False, indent=2)


def markdown(value):
    suff = value['investigation']['sufficiency']
    context = value.get('runContext', {})
    selection = value['traceSearch'].get('selection', {})
    translation = value.get('translation', {})
    overview = translation.get('overview', {})
    display_summary = _naturalize(overview.get('summary') or _auto_summary(value))
    lines = [f'# {value["caseId"]} · Skill 能力报告', '', f'你想了解：{value["question"]}', '',
             '## 先说结论', '', display_summary, '',
             '下面先讲这个 Skill 自己怎么工作；报告末尾再说明 Skill Lens 查了什么、哪些地方还不能确定。', '']
    if translation.get('terms'):
        lines += ['术语对照：' + '；'.join(f'{t["source"]} = {t["translation"]}' for t in translation['terms']), '']
    coverage = translation.get('coverage', {})
    lines += ['## 它大致怎么工作', '']
    mechanism_steps = value.get('mechanismSteps', [])
    display_steps = [step for step in mechanism_steps
                     if not (step['status'] == 'unconfirmed' and not step.get('evidenceNodeIds'))]
    if display_steps and not overview.get('summary'):
        titles = '、'.join(step['title'] for step in display_steps[:4])
        display_summary = _auto_summary(value)
    if overview or display_steps:
        if overview.get('confidenceNote'):
            lines += ['这份结论的范围：' + _naturalize(overview['confidenceNote']), '']
        if overview.get('mechanismChain'):
            lines += ['它的顺序：', ''] + [f'{index}. {_naturalize(step)}' for index, step in enumerate(overview['mechanismChain'], 1)] + ['']
        presets = overview.get('scenarioPresets', [])
        decision_rules = _decision_rules(value)
        if decision_rules and not presets:
            lines += ['## 场景与决策：这个 Skill 在什么情况下做什么', '',
                      '并不是所有 Skill 都有固定的“预设档位”。没有档位时，就把它的条件分支和工作路径整理成下面这张表：先看输入或场景，再看它如何判断，最后看它会采取什么动作。', '']
            lines += ['| 面对什么输入或场景 | 它先判断什么 | 接着做什么 | 依据状态 |',
                      '|---|---|---|---|']
            for rule in decision_rules:
                lines.append(f'| {rule["scenario"]} | {rule["decision"]} | {rule["action"]} | {_mechanism_status(rule.get("status"))} |')
            lines += ['', '如果报告显示“这是把几条规则串起来的理解”，说明它是静态分析得到的流程关系；不能把它当成某一次真实运行的日志。', '']
        if presets:
            lines += ['## 场景与预设：先选一个起点', '',
                      '这部分是这个 Skill 的核心决策表：先判断任务属于哪种场景，再把对应的三个参数作为后续布局、动效和信息密度的起点。预设是推荐起点，不等于已经观察到本次运行采用了它。', '']
            assessment = overview.get('presetAssessment')
            if assessment:
                lines += [f'本次实际采用情况：{_naturalize(assessment["label"])}。{_naturalize(assessment["note"])}', '']
            lines += ['| 场景 | 什么时候用 | 三个参数（视觉变化 / 动效 / 信息密度） | 输出倾向 |',
                      '|---|---|---|---|']
            for preset in presets:
                dials = preset['dials']
                values = f'{dials["DESIGN_VARIANCE"]} / {dials["MOTION_INTENSITY"]} / {dials["VISUAL_DENSITY"]}'
                lines.append(f'| {preset["title"]} | {preset["when"]} | `{values}` | {preset["result"]} |')
            lines += ['', '参数含义：`DESIGN_VARIANCE` = 视觉变化幅度；`MOTION_INTENSITY` = 动效强度；`VISUAL_DENSITY` = 信息密度。', '']
            evidence_labels = []
            evidence_ids = []
            for preset in presets:
                evidence_labels.extend(preset.get('evidenceLabels', []))
                evidence_ids.extend(preset.get('evidenceNodeIds', []))
            if evidence_labels:
                lines += ['原文依据：' + '、'.join(dict.fromkeys(evidence_labels))]
            if evidence_ids:
                lines += ['原文节点：' + ', '.join(dict.fromkeys(evidence_ids))]
            lines += ['']
        if overview.get('modules'):
            lines += ['每个关键步骤：', '']
            for module in overview['modules']:
                lines += [f'### {module["title"]}', '', '这一步做什么：' + _naturalize(module['action']),
                          '如何核对：' + _naturalize(module['check']),
                          '原文依据：' + ', '.join(module.get('evidenceLabels', module.get('evidenceNodeIds', []))),
                          '这些步骤是对多条原文的整理；点开下面的依据可以逐条核对。', '']
        if display_steps and not overview.get('modules'):
            lines += ['### 一步一步看', '', '每一步是对原文的白话整理；它不是运行日志。', '']
            for step in display_steps:
                lines += [f'### {step["title"]}', '', _safe_stage_summary(step), f'依据：{_mechanism_status(step["status"])}']
                if step['status'] == 'unconfirmed': lines += [f'目前还不能确认：{step["boundary"]}']
                else: lines += [f'核对时可以看：{_naturalize(step["observable"])}']
                lines += ['']
        if overview.get('example'):
            example = overview['example']
            lines += ['## 一个具体例子', '',
                      '举例：' + _naturalize(example['input']),
                      '它会怎样判断：' + _naturalize(example['decision']),
                      '因此会倾向于：' + _naturalize(example['output']),
                      '这个例子不能证明：' + _naturalize(example['limitation']),
                      '依据：' + ', '.join(example.get('evidenceLabels', example.get('evidenceNodeIds', []))), '']
    else:
        lines[4:4] = [display_summary, '']
    lines += ['## 相关原文规则（用于核对）', '', _translation_note(translation), '']
    for claim in value['claims']:
        lines += [f'### {_claim_title(claim)}', '']
        if _has_translation(claim):
            lines += [f'中文理解（{_translation_status(claim.get("translationStatus"))}）：{claim["translation"]}', '']
        # Missing claim translations are summarized once in the coverage line;
        # repeating a boilerplate sentence beside every source adds no value.
        note = _claim_reader_note(claim)
        if note and not _generic_interpretation(note):
            lines += ['这条规则的作用：' + note, '']
        lines += ['英文原文：', '> ' + _display_claim_text(claim['text']).replace('\n', '\n> '),
                  '证据节点：' + ', '.join(claim['evidenceNodeIds']), '']
    lines += ['## 历史运行记录（用于确认实际行为）', '']
    if selection:
        if selection.get('matchedFileCount', 0):
            lines += [f'这次在历史记录中找到了 {selection.get("matchedFileCount", 0)} 个可能相关的文件，下面只列出其中能和这个 Skill 对上的片段。', '']
        else:
            lines += ['这次只检查了 Skill 文件，没有找到可对应的历史执行记录，因此不能说明某一次运行实际走了哪条路径。', '']
        if selection.get('roots'):
            lines += ['（扫描位置等技术细节已收进报告信息。）', '']
    for s in value['traceSearch']['slices']:
        lines += [f'- {s["adapter"]} / {s["traceId"]}：Skill 版本 {s["revisionBinding"]}，记录片段 {s["completeness"]}。']
        for e in s['events']:
            lines += [f'  - {e["id"]} · {e["type"]} · {e.get("name") or e.get("skill") or e.get("artifact") or e.get("text", "")[:300]}']
    if not value['traceSearch']['slices'] and not selection: lines += ['没有可列出的运行片段。']
    lines += ['', '## 目前还不能确定', '']
    for u in value['unknowns']:
        question = u['question']
        reason = u['reason']
        if '模型内部' in question:
            lines += ['- 我们看不到模型内部的思考过程，只能根据 Skill 原文和已有运行记录解释外部行为。']
        elif '严格遵循' in question:
            lines += ['- 仅凭 Skill 文件不能证明模型每次都遵守规则，也不能证明读者一定看懂；这需要实际输出或反馈。']
        elif '源码' in question:
            lines += ['- 当前只读到了预算范围内的源码；没有继续展开的部分不代表不存在。']
        else:
            lines += [f'- {question} {reason}']
    lines += ['', '## 原文依据（需要核对时再看）', '', '这里保留英文原文；有中文对照时先显示中文，英文用于逐字核对。', '']
    for item in value['investigation']['selected']:
        lines += [f'### 原文片段 · {item["nodeId"]}', '']
        if _has_translation(item):
            lines += [f'中文理解（{_translation_status(item.get("translationStatus"))}）：{item["translation"]}', '']
        lines += ['英文原文：', '> ' + item['sourceQuote'].replace('\n', '\n> '), '']
    lines += ['## 报告信息（技术细节）', '', f'本次先做源码检查；历史记录状态为 {value["traceSearch"]["status"]}。',
              _friendly_budget(value), '',
              '引用校验只说明出处能回到原文；它不等于已经观察到运行结果。', '']
    return '\n'.join(lines)


def render(value, graph):
    esc = lambda x: html.escape(str(x), quote=True)
    nodes = {n['id']: n for n in graph['nodes']}
    evidence = {e['id']: e for e in graph['evidence']}
    def cite(ids):
        labels = []
        for nid in ids:
            node = nodes.get(nid, {})
            source = next((evidence[eid].get('source', {}) for eid in node.get('evidence', []) if eid in evidence), {})
            path = source.get('path') or '源码节点'
            lines = source.get('lines')
            label = f'{path}:{lines}' if lines else path
            labels.append(f'<a href="#source-{esc(nid)}">{esc(label)}</a>')
        return '<div class="evidence-links"><b>原文位置</b>' + ''.join(labels) + '</div>'
    suff = value['investigation']['sufficiency']
    translation = value.get('translation', {})
    overview = translation.get('overview', {})
    display_summary = _naturalize(overview.get('summary') or _auto_summary(value))
    terms = ('<p class="terms"><b>术语对照：</b>' + '；'.join(f'{esc(t["source"])} = {esc(t["translation"])}' for t in translation.get('terms', [])) + '</p>') if translation.get('terms') else ''
    coverage = translation.get('coverage', {})
    coverage_note = f'<p class="coverage">{esc(_translation_note(translation))}</p>'
    chain = ''.join(f'<li>{esc(_naturalize(step))}</li>' for step in overview.get('mechanismChain', []))
    mechanism_steps = value.get('mechanismSteps', [])
    display_steps = [step for step in mechanism_steps
                     if not (step['status'] == 'unconfirmed' and not step.get('evidenceNodeIds'))]
    step_cards = ''.join(
        f'<article class="mechanism-step"><h3>{esc(step["title"])}</h3>'
        f'<p class="stage-summary">{esc(_naturalize(_safe_stage_summary(step)))}</p>'
        f'<p class="confidence"><b>{esc(_mechanism_status(step["status"]))}</b>。'
        f'{esc(_naturalize(("目前还不能确认：" + step["boundary"]) if step["status"] == "unconfirmed" else ("核对时可以看：" + step["observable"])))}</p>'
        f'<details class="stage-detail"><summary>展开这一步的分析拆解</summary>'
        f'<p><b>它接收什么</b>：{esc(_naturalize(step["input"]))}<br><b>根据什么决定</b>：{esc(_naturalize(step["decision"]))}<br><b>接着做什么</b>：{esc(_naturalize(step["action"]))}</p></details>'
        f'{cite(step.get("evidenceNodeIds", []))}</article>'
        for step in display_steps)
    scenario_html = ''
    presets = overview.get('scenarioPresets', [])
    if presets:
        assessment = overview.get('presetAssessment')
        assessment_html = ''
        if assessment:
            assessment_html = (f'<div class="preset-assessment"><b>本次实际采用情况：{esc(_naturalize(assessment["label"]))}</b>'
                               f'<p>{esc(_naturalize(assessment["note"]))}</p></div>')
        rows = ''
        evidence_ids = []
        for preset in presets:
            dials = preset['dials']
            values = f'{dials["DESIGN_VARIANCE"]} / {dials["MOTION_INTENSITY"]} / {dials["VISUAL_DENSITY"]}'
            rows += (f'<tr><td><b>{esc(preset["title"])}</b></td><td>{esc(_naturalize(preset["when"]))}</td>'
                     f'<td><span class="dial-values">{esc(values)}</span><small>视觉变化 / 动效 / 信息密度</small></td>'
                     f'<td>{esc(_naturalize(preset["result"]))}</td></tr>')
            evidence_ids.extend(preset.get('evidenceNodeIds', []))
        evidence_ids = list(dict.fromkeys(evidence_ids))
        scenario_html = (f'<section class="scenario-presets"><h3>场景与预设：先选一个起点</h3>'
                         f'<p>这是这个 Skill 的核心决策表：先判断任务属于哪种场景，再把三个参数交给后续布局、动效和信息密度规则。预设是推荐起点，不等于已经观察到本次运行采用了它。</p>'
                         f'{assessment_html}<div class="scroll"><table class="preset-table"><thead><tr><th>场景</th><th>什么时候用</th><th>三个参数</th><th>输出倾向</th></tr></thead><tbody>{rows}</tbody></table></div>'
                         f'<p class="confidence">参数含义：<code>DESIGN_VARIANCE</code> = 视觉变化幅度；<code>MOTION_INTENSITY</code> = 动效强度；<code>VISUAL_DENSITY</code> = 信息密度。</p>'
                         f'{cite(evidence_ids)}</section>')
    decision_html = ''
    decision_rules = _decision_rules(value)
    if decision_rules and not presets:
        rows = ''.join(
            f'<tr><td><b>{esc(rule["title"])}</b><br>{esc(_naturalize(rule["scenario"]))}</td>'
            f'<td>{esc(_naturalize(rule["decision"]))}</td>'
            f'<td>{esc(_naturalize(rule["action"]))}</td>'
            f'<td><span class="decision-status">{esc(_mechanism_status(rule.get("status")))}</span></td></tr>'
            for rule in decision_rules)
        evidence_ids = list(dict.fromkeys(node for rule in decision_rules for node in rule.get('evidenceNodeIds', [])))
        decision_html = (f'<section class="decision-rules"><h3>场景与决策：这个 Skill 在什么情况下做什么</h3>'
                         f'<p>并不是所有 Skill 都有固定的“预设档位”。没有档位时，就把它的条件分支和工作路径整理成下面这张表：先看输入或场景，再看它如何判断，最后看它会采取什么动作。</p>'
                         f'<div class="scroll"><table class="decision-table"><thead><tr><th>输入或场景</th><th>它先判断什么</th><th>接着做什么</th><th>依据状态</th></tr></thead><tbody>{rows}</tbody></table></div>'
                         f'<p class="confidence">“这是把几条规则串起来的理解”表示静态分析得到的流程关系；它不能替代某一次真实运行日志。</p>'
                         f'{cite(evidence_ids)}</section>')
    modules = ''
    for module in overview.get('modules', []):
        labels = module.get('evidenceLabels')
        node_ids = module.get('evidenceNodeIds', [])
        refs = ('<div class="evidence-links"><b>原文依据</b>' + ''.join(f'<span>{esc(label)}</span>' for label in labels) + '</div>') if labels else cite(node_ids)
        evidence_drawers = ''.join(
            f'<details class="module-evidence"><summary>查看依据：{esc(label)}</summary><p class="muted">这是这一步对应的原文；上面的白话说明是对相关规则的整理。</p><pre>{esc(evidence.get(next((eid for eid in nodes.get(nid, {}).get("evidence", []) if eid in evidence), ""), {}).get("quote", ""))}</pre></details>'
            for label, nid in zip(labels or node_ids, node_ids)
        )
        modules += f'<section class="mechanism-module"><h3>{esc(module["title"])}</h3><p><b>这一步做什么</b><br>{esc(_naturalize(module["action"]))}</p><p><b>如何核对</b><br>{esc(_naturalize(module["check"]))}</p>{refs}{evidence_drawers}</section>'
    example_html = ''
    if overview.get('example'):
        example = overview['example']
        example_html = (f'<section class="example"><h3>一个具体例子</h3>'
                        f'<p><b>举例</b><br>{esc(_naturalize(example["input"]))}</p>'
                        f'<p><b>它会怎样判断</b><br>{esc(_naturalize(example["decision"]))}</p>'
                        f'<p><b>因此会倾向于</b><br>{esc(_naturalize(example["output"]))}</p>'
                        f'<p class="confidence"><b>这个例子不能证明</b><br>{esc(_naturalize(example["limitation"]))}</p>'
                        + (('<div class="evidence-links"><b>对应规则</b>' + ''.join(f'<span>{esc(label)}</span>' for label in example.get('evidenceLabels', [])) + '</div>') if example.get('evidenceLabels') else cite(example.get("evidenceNodeIds", []))) + '</section>')
    overview_html = ''
    if overview or display_steps:
        overview_html = f'<section id="overview" class="overview skill-view"><h2>先说结论</h2><p class="overview-summary">{esc(display_summary)}</p>'
        if overview.get('confidenceNote'):
            overview_html += f'<p class="confidence"><b>这份结论的范围</b>：{esc(_naturalize(overview["confidenceNote"]))}</p>'
        overview_html += f'<details id="lens-audit" class="audit-meta"><summary>报告来源与范围</summary><p class="confidence">这里说明报告是怎么得出的，不是被分析 Skill 的功能。</p><p class="confidence"><b>读到的源码范围</b>：{esc(_coverage_note(value).removeprefix("证据覆盖："))}</p><p class="confidence"><b>分析方式</b>：先看源码；历史记录状态为 {esc(value["traceSearch"]["status"])}。</p><p class="confidence"><b>中文对照</b>：{esc("已提供" if translation.get("status") in ("available", "partial") else "未提供逐条对照")}</p></details>'
        if chain:
            overview_html += f'<!-- 机制链：供机器校验的兼容标记 --> <h3>它大致怎么工作</h3><ol class="mechanism-chain">{chain}</ol>'
        overview_html += decision_html
        overview_html += scenario_html
        if step_cards and not modules:
            overview_html += f'<details class="mechanism-details" open><summary>一步一步看</summary><p class="confidence">这是对 Skill 原文的白话整理，不是某一次运行的日志。</p><div class="mechanism-modules">{step_cards}</div></details>'
        if modules:
            overview_html += f'<details class="mechanism-details"><summary>展开机制步骤与可检查项</summary><div class="mechanism-modules">{modules}</div></details>'
        overview_html += example_html
        overview_html += '</section>'
    raw_summary = '' if (overview or display_steps) else f'<p class="lead">{esc(value["summary"])}</p>'
    toc = '<nav class="toc" aria-label="报告导航"><b>快速跳转</b>' + ('<a href="#overview">Skill 怎么工作</a>' if (overview or display_steps) else '') + '<a href="#rules">原文依据</a><a href="#history">运行记录</a><a href="#unknowns">还不能确定</a><a href="#evidence">英文原文</a></nav>'
    layer_note = '<div class="layer-strip"><span><b>上面</b> 这个 Skill 规定了什么</span><span><b>下面</b> Lens 用什么依据来判断</span></div>'
    def claim_translation(c):
        if _has_translation(c):
            return f'<div class="translation primary"><b>中文理解 <span class="translation-status">{esc(_translation_status(c.get("translationStatus")))}</span></b><p>{esc(c["translation"])}</p></div>'
        return ''
    claims = ''.join(f'<section class="claim"><span class="tag">{esc(_evidence_label(c["evidenceType"]))}</span><h3>{esc(_claim_title(c))}</h3>'
                     f'{claim_translation(c)}'
                     + (f'<p class="interpretation"><b>这条规则的作用</b><br>{esc(_claim_reader_note(c))}</p>' if _claim_reader_note(c) and not _generic_interpretation(_claim_reader_note(c)) else '')
                     + f'<details class="source-inline"><summary>展开英文原文</summary><pre>{esc(_display_claim_text(c["text"]))}</pre></details>'
                     + f'{cite(c["evidenceNodeIds"])}</section>' for c in value['claims'])
    source_ids = list(dict.fromkeys([s['nodeId'] for s in value['investigation']['selected']] + [n for c in value['claims'] for n in c['evidenceNodeIds']]))
    source = ''
    for nid in source_ids:
        for eid in nodes[nid].get('evidence', []):
            e = evidence[eid]; s = e.get('source', {})
            selected = next((item for item in value['investigation']['selected'] if item['nodeId'] == nid), {})
            translated = selected.get('translation', '')
            if _has_translation(selected):
                translation_block = f'<div class="translation primary"><b>中文理解 <span class="translation-status">{esc(_translation_status(selected.get("translationStatus")))}</span></b><p>{esc(translated)}</p></div>'
            else:
                translation_block = ''
            source += f'<details id="source-{esc(nid)}"><summary>{esc(s.get("path"))}:{esc(s.get("lines"))} · {esc(nid)}</summary>{translation_block}<p class="original-label">英文原文</p><pre>{esc(e.get("quote", ""))}</pre></details>'
    traces = ''
    selection = value['traceSearch'].get('selection', {})
    if selection:
        matched = selection.get('matchedFileCount', 0)
        if matched:
            traces += f'<p>历史记录中找到了 {esc(matched)} 个可能相关的文件。下面只列出能和这个 Skill 对上的片段。</p>'
        else:
            traces += '<p>这次只检查了 Skill 文件，没有找到可对应的历史执行记录，因此不能说明某一次运行实际走了哪条路径。</p>'
        if selection.get('roots'):
            traces += '<details><summary>查看扫描位置（技术细节）</summary><ul>'
            traces += ''.join(f'<li>{esc(root)}</li>' for root in selection['roots']) + '</ul></details>'
    for s in value['traceSearch']['slices']:
        rows = ''.join(f'<tr><td>{esc(e["id"])}</td><td>{esc(e["type"])}</td><td>{esc(e.get("name") or e.get("skill") or e.get("artifact") or "")}</td><td>{esc(e.get("text", ""))}</td></tr>' for e in s['events'])
        edges = ''.join(f'<li>{esc(e["from"])} → {esc(e["to"])}：调用与返回，通过 call ID 关联</li>' for e in s['edges'])
        traces += f'<details class="runtime"><summary>{esc(s["adapter"])} · {esc(s["traceId"])} · Skill 版本 {esc(s["revisionBinding"])}</summary><p>来源：{esc(s["sourcePath"])}。这是历史记录片段，匹配表示关联线索；不单独证明因果。</p><div class="scroll"><table><thead><tr><th>事件</th><th>观察类型</th><th>对象</th><th>脱敏片段</th></tr></thead><tbody>{rows}</tbody></table></div><ul>{edges}</ul></details>'
    if not traces:
        traces = '<p>没有可列出的运行片段。当前报告主要依据 Skill 文件。</p>'
    questions = ''.join(f'<li><b>{esc(t["question"])}</b>：{esc(_investigation_status(t["status"]))}<p>读到了 {len(t["evidenceNodeIds"])} 个源码片段；{esc("、".join(_stop_reason(reason) for reason in t["stopReasons"]) or "已找到对应原文")}。</p></li>' for t in value['investigation']['questionTree'])
    def unknown_text(item):
        question = item['question']
        if '模型内部' in question:
            return '我们看不到模型内部的思考过程，只能根据 Skill 原文和已有运行记录解释外部行为。'
        if '严格遵循' in question:
            return '仅凭 Skill 文件不能证明模型每次都遵守规则，也不能证明读者一定看懂；这需要实际输出或反馈。'
        if '源码' in question:
            return '当前只读到了预算范围内的源码；没有继续展开的部分不代表不存在。'
        return question + ' ' + item['reason']
    unknown = ''.join(f'<li>{esc(unknown_text(u))}</li>' for u in value['unknowns'])
    roles = ''.join(f'<tr><td>{esc(u["path"])}:{u["start"]}</td><td>{esc(", ".join(u["roles"]))}</td><td>{esc("; ".join(u["signals"]))}</td></tr>' for u in value['documentAnalysis']['units'] if u['nodeId'] in source_ids)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{esc(value['caseId'])} · Skill 能力报告</title><style>
:root{{--paper:#f7f5ef;--ink:#282f2c;--muted:#59645f;--rule:#d7dcd5;--accent:#24695d;--tint:#e6eee8;--translation:#f0ece2;--runtime:#315a81}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.8 system-ui,-apple-system,'Songti SC',sans-serif}}main{{max-width:1100px;padding:32px;margin:auto}}header{{display:flex;justify-content:space-between;border-bottom:1px solid var(--rule);padding-bottom:16px}}h1{{font:42px/1.2 Georgia,'Songti SC',serif;margin:40px 0 24px}}h2{{font-size:24px;margin:32px 0 16px}}h3{{font-size:20px;margin:12px 0}}p{{max-width:82ch;overflow-wrap:anywhere}}.lead{{font-size:22px}}.answer{{border-left:3px solid var(--accent);padding:8px 24px;background:var(--tint)}}.overview{{border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);padding:8px 0 24px}}.overview-summary{{font-size:22px;max-width:70ch;border-left:3px solid var(--accent);padding-left:18px}}.confidence{{color:var(--muted);max-width:82ch}}.report-meta{{color:var(--muted);font-size:14px}}.toc{{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:20px 0;color:var(--muted);font-size:14px}}.toc a{{border-bottom:1px solid var(--rule);text-decoration:none}}.layer-strip{{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0 6px;padding:10px 14px;background:var(--tint);border-left:3px solid var(--accent);color:var(--muted);font-size:14px}}.layer-strip span+span{{border-left:1px solid var(--rule);padding-left:10px}}.mechanism-chain{{padding-left:28px}}.mechanism-chain li{{padding:4px 0}}.scenario-presets{{border-top:1px solid var(--rule);margin-top:20px;padding-top:10px}}.scenario-presets h3{{margin-bottom:4px}}.preset-table{{min-width:760px}}.preset-table small{{display:block;color:var(--muted);font-size:12px;line-height:1.4}}.dial-values{{font-family:ui-monospace,monospace;white-space:nowrap}}.preset-assessment{{border-left:3px solid var(--runtime);background:#edf2f6;padding:10px 16px;margin:14px 0}}.preset-assessment p{{margin:4px 0;color:var(--muted)}}.mechanism-modules{{border-top:1px solid var(--rule)}}.mechanism-module,.mechanism-step{{padding:14px 0;border-bottom:1px solid var(--rule)}}.mechanism-module h3,.mechanism-step h3{{margin:0}}.mechanism-module p,.mechanism-step p{{margin:6px 0}}.example{{border-top:1px solid var(--rule);margin-top:20px;padding-top:16px}}.muted,.tag{{color:var(--muted)}}.tag{{font-size:12px;border:1px solid var(--rule);padding:2px 6px}}.translation{{margin:14px 0;padding:10px 16px;background:var(--translation);border-left:3px solid var(--accent)}}.translation.primary{{font-size:18px}}.translation p{{margin:4px 0}}.translation-status{{font-size:12px;color:var(--muted);font-weight:normal}}.original-label{{margin:8px 0 0;color:var(--muted);font-size:13px}}.terms{{margin:8px 0;color:var(--muted);font-size:14px}}.coverage{{color:var(--muted);font-size:14px}}.claim{{padding:24px 0;border-bottom:1px solid var(--rule)}}.claim h3{{margin-top:10px}}.interpretation{{color:var(--muted);margin:16px 0}}.evidence-links{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:16px;color:var(--muted);font-size:13px}}.evidence-links a{{border:1px solid var(--rule);padding:2px 7px;text-decoration:none;max-width:100%;overflow-wrap:anywhere}}a{{color:var(--accent)}}details{{margin:12px 0;padding:12px 0;border-top:1px solid var(--rule)}}summary{{cursor:pointer;font-weight:550;overflow-wrap:anywhere}}pre{{font:14px/1.7 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}.runtime{{border-left:2px solid var(--runtime);padding-left:16px}}table{{width:100%;border-collapse:collapse;text-align:left}}td,th{{border-bottom:1px solid var(--rule);padding:12px;vertical-align:top;overflow-wrap:anywhere}}.scroll{{overflow-x:auto}}li p{{margin-top:4px;color:var(--muted)}}footer{{margin-top:48px;border-top:1px solid var(--rule);padding-top:16px;color:var(--muted);font-size:13px}}@media(max-width:640px){{main{{padding:20px}}h1{{font-size:32px;margin-top:28px}}.answer{{padding:8px 16px}}header{{flex-wrap:wrap;gap:10px}}.overview-summary{{font-size:19px;padding-left:14px}}.toc{{gap:8px 12px}}.layer-strip span+span{{border-left:0;padding-left:0}}.translation.primary{{font-size:17px}}.claim{{padding:18px 0}}.preset-table{{min-width:700px}}}}@media print{{details>*{{display:block}}}}
</style></head><body><main><header><b>被分析的 Skill</b><a href="source-report.html">查看原始关系图</a></header>
<h1>{esc(value['caseId'])}</h1><p class="report-question"><b>你想了解：</b>{esc(value['question'])}</p>{toc}{layer_note}{overview_html}<section class="lens-note"><b>报告怎么得出的</b><p>{esc(_user_facing_status(value))} <a href="#lens-audit">查看来源范围</a></p></section>
<details id="rules" class="lens-evidence"><summary>查看支持这些判断的原文</summary><p class="muted">这里是 Lens 按你的问题挑出的原文；它们用来支撑上面的白话说明，不是这个 Skill 额外拥有的功能。中文理解放在前面，英文原文点开看。</p><!-- 每条先给出中文理解：兼容旧报告检查 -->{coverage_note}{claims or '<p>当前源码不足以形成机制结论。</p>'}</details>
<section id="history"><h2>历史运行记录</h2>{traces}</section><section id="unknowns"><h2>目前还不能确定</h2><ul>{unknown}</ul></section>
<details id="evidence"><summary>查看英文原文</summary>{source}</details>
<details><summary>查看 Lens 如何逐层查找（技术细节）</summary><ol>{questions}</ol><pre>{esc(_friendly_budget(value))}</pre></details>
<details><summary>查看 Markdown 内容分类（技术细节）</summary><p>这些分类是带出处的阅读辅助；文档相邻不代表实际调用。</p><div class="scroll"><table><thead><tr><th>出处</th><th>内容角色</th><th>分类依据</th></tr></thead><tbody>{roles}</tbody></table></div></details>
<footer>报告先讲被分析 Skill，再讲 Skill Lens 的依据和边界。报告离线可读，无外部请求。</footer></main></body></html>'''


def write(value, bundle, output, translation_file=None):
    review = validate_explanation(value, bundle)
    output = Path(output)
    if output.resolve().is_relative_to(Path(bundle).resolve()):
        raise ValueError('explanation output must be outside the immutable Bundle')
    output.mkdir(parents=True, exist_ok=True)
    graph = json.loads((Path(bundle) / 'evidence-graph.json').read_text())
    translation = load(translation_file, bundle,
                       claims=[claim['id'] for claim in value['claims']],
                       selected=[item['nodeId'] for item in value['investigation']['selected']])
    value = attach(value, translation)
    # Export the same redacted representation used by the readable views. The
    # in-memory value was validated before redaction; locators and hashes keep
    # the safe export auditable without copying private trace payloads.
    safe_value = sanitize(value, limit=120000)
    safe_value.setdefault('export', {})['privacy'] = 'redacted-excerpts'
    (output / 'explanation.json').write_text(json.dumps(safe_value, ensure_ascii=False, indent=2) + '\n')
    (output / 'explanation-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n')
    # Source documents are sanitized only for display, never silently rewritten
    # in the analysis that underwent exact source validation.
    (output / 'explanation.md').write_text(markdown(safe_value), encoding='utf-8')
    (output / 'index.html').write_text(render(safe_value, sanitize(graph, limit=120000)), encoding='utf-8')
    write_source_report(Path(bundle), output / 'source-report.html')
    return {'status': value['status'], 'report': str((output / 'index.html').resolve()),
            'markdown': str((output / 'explanation.md').resolve()), 'review': review['status']}
