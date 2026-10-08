#!/usr/bin/env python3
"""Review a graph-bound mechanism sidecar for evidence and coverage signals.

This is deliberately a deterministic review, not a semantic judge.  It reports
which guarantees come from the Evidence Gate and which questions still need a
human or runtime evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from instruction_analysis import validate_analysis
from validate_lens_bundle import validate


def review(bundle: Path, analysis_path: Path) -> dict:
    baseline = validate(bundle)
    if not baseline['valid']:
        raise ValueError('invalid Bundle: ' + '; '.join(baseline['errors']))
    graph_path = bundle / 'evidence-graph.json'
    graph_bytes = graph_path.read_bytes()
    graph = json.loads(graph_bytes)
    analysis = json.loads(analysis_path.read_text(encoding='utf-8'))
    validated = validate_analysis(analysis, graph, graph_bytes)

    evidence = {item['id']: item for item in graph.get('evidence', [])}
    markdown_nodes = {
        node['id']: node for node in graph.get('nodes', [])
        if node.get('attributes', {}).get('kind') == 'markdown-span'
        and node.get('evidence')
    }
    cited: set[str] = set()
    for section in ('stages', 'prompts', 'audiences', 'illustrations'):
        for item in validated[section]:
            cited.update(item.get('evidenceNodeIds', []))
            for rule in item.get('rules', []):
                cited.update(rule.get('evidenceNodeIds', []))
    cited_markdown = cited & set(markdown_nodes)
    block_types = sorted({
        markdown_nodes[node_id].get('attributes', {}).get('blockType')
        for node_id in cited_markdown
    })
    source_paths = sorted({
        str(evidence[ref].get('source', {}).get('path', ''))
        for node_id in cited_markdown
        for ref in markdown_nodes[node_id].get('evidence', [])
        if ref in evidence
    })
    total_markdown = len(markdown_nodes)
    coverage = (len(cited_markdown) / total_markdown) if total_markdown else 0.0
    warnings: list[str] = []
    if len(validated['stages']) < 2:
        warnings.append('机制阶段少于两步，可能没有把规则组织成可读流程。')
    if not validated['unknowns']:
        warnings.append('没有 unknowns；静态源码不能证明的运行结果应明确列出。')
    if total_markdown and not cited_markdown:
        warnings.append('没有引用 Markdown source span。')
    if cited_markdown and set(block_types) <= {'heading_open'}:
        warnings.append('引用全部是标题；标题不能单独证明行为规则。')
    if any(not item.get('rules') for item in validated['audiences']):
        warnings.append('至少一个受众没有细分规则；如果源码没有明确规则，保留为空是正确的。')

    return {
        'schema': 'skill-lens.instruction-analysis-review.v0.1',
        'bundle': str(bundle),
        'analysis': str(analysis_path),
        'graphSha256': hashlib.sha256(graph_bytes).hexdigest(),
        'status': 'reviewed',
        'evidenceGate': {
            'status': 'passed',
            'stageCount': len(validated['stages']),
            'promptGroupCount': len(validated['prompts']),
            'audienceCount': len(validated['audiences']),
            'illustrationCount': len(validated['illustrations']),
        },
        'coverage': {
            'citedMarkdownSpanCount': len(cited_markdown),
            'totalMarkdownSpanCount': total_markdown,
            'ratio': round(coverage, 4),
            'blockTypes': block_types,
            'sourcePaths': source_paths,
        },
        'semanticLimits': [
            '引用正确不等于语义解释正确。',
            '源码要求不等于模型一定遵循。',
            '静态证据不等于读者真的理解。',
        ],
        'warnings': warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('analysis', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = review(args.bundle, args.analysis)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    print(rendered, end='')


if __name__ == '__main__':
    main()
