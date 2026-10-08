#!/usr/bin/env python3
"""Check a relocated installation using only original synthetic source."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(tool: str, *args: object) -> str:
    result = subprocess.run([sys.executable, str(ROOT / "tools" / tool),
                             *map(str, args)], cwd=ROOT, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-providers", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="skill-lens-install-") as directory:
        workspace = Path(directory)
        source = workspace / "source"
        source.mkdir()
        (source / "main.py").write_text("def greet():\n    return 'hello'\n\ngreet()\n")
        (source / "SKILL.md").write_text("---\nname: smoke-example\ndescription: Return a greeting.\n---\n\n# Greeting\n\nUse main.py to return a greeting.\n")
        if args.auto_providers:
            (source / "worker.ts").write_text("function greet(): string { return 'hello'; }\ngreet();\n")
        bundle = workspace / "bundle"
        cache = workspace / "cache"
        providers = ["--auto-providers"] if args.auto_providers else ["--provider", "stdlib-baseline", "--provider", "python-ast"]
        # This is a synthetic snapshot identifier, not an upstream commit.
        run("skill_lens.py", "inspect", "--source-dir", source,
            "--case-id", "install-smoke", "--revision", "0" * 40,
            *providers, "--cache-dir", cache, "--out", bundle)
        diagnosis = json.loads(run("skill_lens.py", "diagnose", bundle))
        assert diagnosis["valid"], diagnosis
        graph = json.loads((bundle / "evidence-graph.json").read_text())
        assert any(node["label"] == "main.py:greet" for node in graph["nodes"])
        manifest = json.loads((bundle / "analysis-manifest.json").read_text())
        assert not manifest.get("fallbackReason"), manifest
        run("skill_lens.py", "ask", bundle, "greet", "--limit", 3)
        run("skill_lens.py", "impact", bundle, "--path", "main.py", "--max-depth", 2)
        report = workspace / "report.md"
        run("render_bundle_explanation.py", bundle, "--source-root", source, "--output", report)
        assert "main.py" in report.read_text()
        visual = workspace / 'report.html'
        run('render_bundle_visualization.py', bundle, '--output', visual)
        assert 'lens-data' in visual.read_text() and 'main.py' in visual.read_text()
        cached = workspace / "cached"
        run("skill_lens.py", "inspect", "--source-dir", source,
            "--case-id", "install-smoke", "--revision", "0" * 40,
            *providers, "--cache-dir", cache, "--out", cached)
        second = json.loads((cached / "analysis-manifest.json").read_text())
        for record in second["cache"]["records"]:
            if record["provider"] in {"stdlib-baseline", "python-ast", "typescript-compiler", "markdown-sections"}:
                assert record["status"] == "hit", record
        print(json.dumps({"valid": True, "autoProviders": args.auto_providers,
                          "nodes": len(graph["nodes"]), "checks": ["inspect", "diagnose", "ask", "impact", "render", "visual-report", "cache"]}))


if __name__ == "__main__":
    main()
