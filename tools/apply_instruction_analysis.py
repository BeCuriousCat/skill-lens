#!/usr/bin/env python3
"""Validate and atomically attach a model-produced instruction analysis."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from instruction_analysis import load_analysis, validate_analysis
from validate_lens_bundle import validate


def apply(bundle: Path, analysis_path: Path) -> dict:
    baseline = validate(bundle)
    if not baseline['valid']:
        raise ValueError('invalid Bundle: ' + '; '.join(baseline['errors']))
    graph_path = bundle / 'evidence-graph.json'
    graph_bytes = graph_path.read_bytes()
    analysis = json.loads(analysis_path.read_text(encoding='utf-8'))
    validated = validate_analysis(analysis, json.loads(graph_bytes), graph_bytes)
    destination = bundle / 'instruction-analysis.json'
    fd, temporary = tempfile.mkstemp(prefix='.instruction-analysis-', dir=bundle)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(analysis, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise
    if not load_analysis(bundle):
        raise ValueError('instruction analysis was not readable after publication')
    return {'bundle': str(bundle), 'analysis': str(destination), 'stageCount': len(validated['stages']),
            'audienceCount': len(validated['audiences']), 'status': 'published'}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('analysis', type=Path)
    args = parser.parse_args()
    print(json.dumps(apply(args.bundle, args.analysis), ensure_ascii=False))


if __name__ == '__main__':
    main()
