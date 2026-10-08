#!/usr/bin/env python3
"""Render a validated Lens Bundle as an offline, evidence-preserving report."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

from validate_lens_bundle import validate
from instruction_analysis import load_analysis, meaning_objects

ROOT = Path(__file__).resolve().parent


def payload_for(bundle: Path, analysis_path: Path | None = None) -> dict:
    result = validate(bundle)
    if not result['valid']:
        raise ValueError('Invalid Bundle: ' + '; '.join(result['errors']))
    read = lambda name: json.loads((bundle / name).read_text(encoding='utf-8'))
    graph, projection = read('evidence-graph.json'), read('semantic-projection.json')
    manifest = read('analysis-manifest.json')
    nodes = {node['id']: node for node in graph['nodes']}
    evidence = {ref['id']: ref for ref in graph['evidence']}
    objects = []
    for obj in projection['objects']:
        refs = list(dict.fromkeys(ref for node in obj['evidenceNodeIds']
                                 for ref in nodes[node].get('evidence', [])))
        if not refs or any(ref not in evidence for ref in refs):
            raise ValueError(f"Projection object lacks valid source Evidence: {obj['id']}")
        objects.append({**obj, 'evidenceIds': refs})
    # Keep actual edge attributes (guards, call-site qualifiers) and citation
    # identities. Provider-specific extension blobs are not presentation data.
    payload = {
        'caseId': graph['caseId'], 'revision': graph['revision'],
        'status': manifest.get('status', 'unknown'),
        'projectionMode': manifest.get('projection', 'unknown'),
        'provenance': projection.get('provenance'),
        'providers': manifest.get('completedProviders', [manifest.get('provider', {})]),
        'objects': objects,
        'nodes': [{**{key: node[key] for key in ('id', 'type', 'label', 'evidence') if key in node},
                   'sourceContext': {key: node.get('attributes', {})[key]
                                     for key in ('kind', 'blockType', 'headingPath')
                                     if key in node.get('attributes', {})}}
                  for node in graph['nodes']],
        'edges': [{key: edge[key] for key in ('id', 'from', 'to', 'type', 'evidence', 'attributes') if key in edge}
                  for edge in graph['edges']],
        'evidence': [{key: ref[key] for key in ('id', 'sourceType', 'source', 'quote', 'confidence') if key in ref}
                     for ref in graph['evidence']],
        'diagnostics': read('diagnostics.json').get('diagnostics', []),
        'hashes': {name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
                   for name in ('evidence-graph.json', 'semantic-projection.json')},
    }
    analysis = load_analysis(bundle, analysis_path)
    if analysis:
        payload['instructionAnalysis'] = analysis
        payload['meaningObjects'] = meaning_objects(analysis)
        analysis_file = analysis_path if analysis_path is not None else bundle / 'instruction-analysis.json'
        payload['hashes']['instruction-analysis.json'] = hashlib.sha256(analysis_file.read_bytes()).hexdigest()
    return payload


def instruction_overview(payload: dict) -> str:
    """Keep the entire core explanation readable without JavaScript."""
    analysis = payload.get('instructionAnalysis')
    if not analysis:
        return ''
    safe = lambda value: html.escape(str(value), quote=True)
    evidence = {ref['id']: ref for ref in payload['evidence']}

    def quotes(item):
        return ''.join(f'<details class="mechanism-evidence"><summary>核对原始提示词 · '
                       f'{safe(evidence[ref]["source"]["path"])}:{safe(evidence[ref]["source"]["lines"])}</summary>'
                       f'<pre>{safe(evidence[ref]["quote"])}</pre></details>' for ref in item['evidenceIds'])

    def prompt(item):
        refs = [evidence[ref] for ref in item['evidenceIds']]
        return (f'<details class="prompt-block" open><summary>{safe(item["label"])}</summary>'
                + ''.join(f'<p class="technical">{safe(ref["source"]["path"])}:{safe(ref["source"]["lines"])}</p>'
                          f'<pre>{safe(ref["quote"])}</pre>' for ref in refs) + '</details>')

    count = len(analysis['stages'])
    width, gap, margin = 1280, 32, 24
    box_width = ((width - 2 * margin - gap * (count - 1)) // count // 4) * 4
    svg = ['<div class="diagram-scroll"><svg viewBox="0 0 1280 208" style="min-width:1280px" role="img" '
           'aria-labelledby="mechanism-title mechanism-desc"><title id="mechanism-title">提示词作用拆解</title>'
           '<desc id="mechanism-desc">分析者根据原文组织的指令作用链；不是运行轨迹，也不是模型内部思考过程。</desc>'
           '<defs><marker id="mechanism-arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">'
           '<polygon points="0 0,8 3,0 6" fill="var(--muted)"/></marker></defs>']
    for index in range(count - 1):
        x = margin + index * (box_width + gap) + box_width
        svg.append(f'<line x1="{x}" y1="84" x2="{x+gap}" y2="84" class="diagram-connector" '
                   'stroke-dasharray="5 4" marker-end="url(#mechanism-arrow)"/>')
    for index, stage in enumerate(analysis['stages']):
        x = margin + index * (box_width + gap)
        # Labels in this overview are deliberately bounded; full text follows.
        budget = max(4, min(9, (box_width - 32) // 16))
        label = stage['label'] if len(stage['label']) <= budget else stage['label'][:budget-1] + '…'
        svg.append(f'<g><title>{safe(stage["label"])}</title><rect x="{x}" y="32" width="{box_width}" height="104" rx="6" '
                   f'class="diagram-node {"diagram-focal" if index == 1 else ""}"/>'
                   f'<text x="{x+16}" y="60" class="svg-label">{index+1:02d} · 指令</text>'
                   f'<text x="{x+16}" y="96">{safe(label)}</text></g>')
    svg.append('<text x="24" y="184" class="svg-label">虚线 = 解释的组织顺序 · 不代表真实执行顺序</text></svg></div>')

    stages = ''.join(f'<section class="mechanism-stage" id="mechanism-stage-{index}">'
                     f'<div class="stage-number">{index+1:02d}</div><div><h3>{safe(stage["label"])}</h3>'
                     f'<p><span class="claim-tag">原文要求</span>{safe(stage["instruction"])}</p>'
                     f'<p><span class="claim-tag interpretation">作用解释</span>{safe(stage["interpretation"])}</p>'
                     f'{quotes(stage)}</div></section>' for index, stage in enumerate(analysis['stages']))
    audiences = ''
    if analysis['audiences']:
        def audience_row(index, item):
            rules = ''.join(f'<li><strong>{safe(rule["label"])}</strong>：{safe(rule["instruction"])}</li>'
                            for rule in item.get('rules', []))
            body = f'<ul>{rules}</ul>' if rules else safe(item['instruction'])
            return (f'<tr><th scope="row"><a href="#meaning-browser" data-meaning-target="meaning:audience:{index}">'
                    f'{safe(item["label"])}</a></th><td>{body}{quotes(item)}</td></tr>')
        audiences = ('<h3>读者角色怎样改变表达</h3><div class="audience-table"><table><thead><tr><th scope="col">读者</th>'
                     '<th scope="col">源码规定的表达方式</th></tr></thead><tbody>'
                     + ''.join(audience_row(index, item) for index, item in enumerate(analysis['audiences'])) + '</tbody></table></div>'
                     '<p class="diagram-caption">点击读者名称，查看它对应的表达规则。这里的角色是解释对象，不是执行任务的多个 Agent。</p>')
    illustrations = ''
    if analysis['illustrations']:
        illustrations = ('<section class="illustrations"><h3>同一个主题，换个读者会怎样说</h3>'
                         '<p class="reading-note">以下由本次分析编写，用来说明规则的作用。不是目标 Skill 的执行记录，不能证明实际效果。</p>'
                         '<div class="example-selector" hidden></div>'
                         + ''.join(f'<section class="illustration" data-example="{index}"><h4>{safe(item["audience"])}</h4>'
                                   f'<p class="subtle">示意输入：{safe(item["input"])}</p><blockquote>{safe(item["output"])}</blockquote>'
                                   f'<p>{safe(item["rationale"])}</p>{quotes(item)}</section>'
                                   for index, item in enumerate(analysis['illustrations'])) + '</section>')
    unknowns = '<ul>' + ''.join(f'<li><strong>{safe(item["question"])}</strong><p>{safe(item["detail"])}</p></li>'
                              for item in analysis['unknowns']) + '</ul>'
    return (f'<section class="instruction-overview" aria-labelledby="instruction-title">'
            f'<p class="kind">核心机制 · 基于原文的分析者解读</p><h2 id="instruction-title">{safe(analysis["title"])}</h2>'
            f'<p class="lead">{safe(analysis["summary"])}</p><p class="variant-note">{safe(analysis["scopeNote"])}</p>'
            f'<p class="technical">分析对象：{safe(analysis["identity"])}</p>'
            + ''.join(svg) + '<div class="mechanism-stages">' + stages + '</div>'
            '<section class="principle"><h3>为什么这些提示词能够影响 Agent 的表达？</h3>'
            '<p>可以把 Skill 理解成给已有语言模型的一份写作任务书。它告诉模型：讲什么、听众知道多少、该选哪些重点、用什么语言和形式表达。'
            '模型已有的解释与写作能力承担实际生成；这些指令用于引导它选择适合读者的表达。</p>'
            '<p>前提是宿主把指令交给模型，并且模型遵循了它们。这是在解释提示词的工作方式，'
            '并没有从此源码观察到宿主加载过程或模型内部推理，也不能据此保证读者一定看懂。</p></section>'
            + audiences + illustrations + '<h3>原始提示词，直接看它怎么写</h3>' + ''.join(prompt(item) for item in analysis['prompts'])
            + '<section class="mechanism-unknowns"><h3>仍然没有被证明的部分</h3>' + unknowns + '</section></section>')


def render(bundle: Path, analysis_path: Path | None = None) -> str:
    payload = payload_for(bundle, analysis_path)
    safe = lambda value: html.escape(str(value), quote=True)
    # Never let source-controlled strings terminate a script data element.
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    data = data.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    data = data.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    fallback = ''.join(f"<details><summary>{safe(obj['label'])}</summary><p>{safe(obj['summary'])}</p>"
                       f"<p>引用节点：{safe(', '.join(obj['evidenceNodeIds']))}</p></details>"
                       for obj in payload['objects'][:12])
    if len(payload['objects']) > 12:
        fallback += '<p>静态预览显示前 12 项。启用 JavaScript 可搜索全部解释与展开源码证据。</p>'
    template = (ROOT / 'visual_report_template.html').read_text(encoding='utf-8')
    # Replace each marker once; untrusted payload cannot create a later marker
    # that is then interpreted as template syntax.
    values = {'CSS': (ROOT / 'visual_report.css').read_text(encoding='utf-8'),
              'JS': (ROOT / 'visual_report.js').read_text(encoding='utf-8'),
              'TITLE': safe(payload['caseId']), 'REVISION': safe(payload['revision']),
              'FALLBACK': fallback, 'DATA': data, 'MECHANISM': instruction_overview(payload)}
    import re
    return re.sub(r'@@(CSS|JS|TITLE|REVISION|FALLBACK|DATA|MECHANISM)@@', lambda match: values[match[1]], template)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--instruction-analysis', type=Path)
    args = parser.parse_args()
    print(json.dumps(write_report(args.bundle, args.output, args.instruction_analysis)))


def write_report(bundle: Path, output: Path, analysis_path: Path | None = None) -> dict:
    if output.suffix.lower() != '.html' or output.resolve().is_relative_to(bundle.resolve()):
        raise ValueError('Write the .html report outside the input Bundle directory')
    document = render(bundle, analysis_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding='utf-8')
    return {'output': str(output), 'bytes': len(document.encode('utf-8')),
            'format': 'offline-html', 'relationships': 'citations and original graph edges; instruction interpretation is separately labeled'}


if __name__ == '__main__':
    main()
