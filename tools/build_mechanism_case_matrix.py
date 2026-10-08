#!/usr/bin/env python3
"""Build bounded mechanism prompts for a directory of Lens Bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_mechanism_prompt import build


def build_matrix(root: Path, output_dir: Path, question: str, max_spans: int, max_chars: int) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for bundle in sorted(path for path in root.iterdir() if path.is_dir()):
        if not (bundle / 'evidence-graph.json').is_file():
            continue
        prompt = build(bundle, question, max_spans, max_chars)
        destination = output_dir / f'{bundle.name}-mechanism-prompt.txt'
        destination.write_text(prompt, encoding='utf-8')
        identity_start = prompt.index('Bundle identity (copy these values exactly):')
        identity_text = prompt[identity_start:].split('\n\nRequired JSON shape', 1)[0]
        identity = json.loads(identity_text.split('\n', 1)[1])
        cases.append({
            'caseId': identity['caseId'],
            'bundle': str(bundle),
            'prompt': str(destination),
            'promptBytes': destination.stat().st_size,
            'sourceRecordCount': identity['sourceRecordCount'],
            'sourceRecordTotal': identity['sourceRecordTotal'],
            'sourceRecordOmitted': identity['sourceRecordOmitted'],
            'selectedSourcePaths': identity['selectedSourcePaths'],
        })
    result = {
        'schema': 'skill-lens.mechanism-case-matrix.v0.1',
        'question': question,
        'maxSpans': max_spans,
        'maxChars': max_chars,
        'cases': cases,
    }
    (output_dir / 'matrix.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='directory containing one Bundle per child directory')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--question', default='核心机制、输入、输出约束与受众如何改变表达？')
    parser.add_argument('--max-spans', type=int, default=180)
    parser.add_argument('--max-chars', type=int, default=28000)
    args = parser.parse_args()
    if args.max_spans < 1 or args.max_chars < 1000:
        raise SystemExit('--max-spans must be positive and --max-chars must be at least 1000')
    print(json.dumps(build_matrix(args.root, args.output_dir, args.question, args.max_spans, args.max_chars), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
