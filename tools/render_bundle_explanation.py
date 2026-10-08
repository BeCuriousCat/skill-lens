#!/usr/bin/env python3
"""Render Projection prose and its citations without inventing semantic links."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

from validate_lens_bundle import validate


SECTIONS = (
    ("Capability", "能做什么"),
    ("Component", "由哪些部分组成"),
    ("Scenario", "流程如何衔接"),
    ("Ownership", "谁负责哪些行为"),
    ("Limitation", "已知限制"),
    ("Uncertainty", "仍然不知道什么"),
)


def plain(value: object) -> str:
    text = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", text).replace("\n", " ")


def source_label(source: dict, source_root: Path | None) -> str:
    path = source.get("path", "?")
    lines = str(source.get("lines", "?"))
    label = plain(f"{path}:{lines}")
    if source_root is not None:
        root = source_root.resolve()
        target = (root / path).resolve()
        # Evidence may describe a tree or an unavailable source. Never turn
        # it into a link outside the explicitly supplied source directory.
        if target.is_relative_to(root) and target.is_file() and not any(c in str(target) for c in "<>\n\r"):
            match = re.match(r"([1-9]\d*)", lines)
            suffix = f":{match.group(1)}" if match else ""
            return f"[{label}](<{target}{suffix}>)"
    return label


def render(bundle: Path, source_root: Path | None = None) -> str:
    result = validate(bundle)
    if not result["valid"]:
        raise ValueError("Invalid Bundle: " + "; ".join(result["errors"]))
    graph = json.loads((bundle / "evidence-graph.json").read_text(encoding="utf-8"))
    projection = json.loads((bundle / "semantic-projection.json").read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "analysis-manifest.json").read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in graph["nodes"]}
    evidence = {ref["id"]: ref for ref in graph["evidence"]}
    used: dict[str, int] = {}
    object_refs = {}
    for obj in projection["objects"]:
        refs = list(dict.fromkeys(ref for node in obj["evidenceNodeIds"] for ref in nodes[node].get("evidence", [])))
        if not refs:
            raise ValueError(f"Projection object has no source Evidence: {obj['id']}")
        for ref in refs:
            if ref not in evidence:
                raise ValueError(f"Missing source Evidence: {ref}")
            used.setdefault(ref, len(used) + 1)
        object_refs[obj["id"]] = refs
    lines = [
        f"# {plain(projection['caseId'])}：Skill Lens 透视结果", "",
        "下面的文字直接来自 Bundle 的语义解释层，按用途分组；渲染器只排版和展开引用。它没有生成新的结论或执行顺序。", "",
        "这些是对保留源码与文档的静态解释，不是一次真实运行的记录。引用有效只说明能定位证据，仍需核查证据是否支持整句话。", "",
        f"源码版本：`{projection['revision']}`。解释模式：{plain(manifest.get('projection', 'unknown'))}。", "",
    ]
    for kind, title in SECTIONS:
        objects = [obj for obj in projection["objects"] if obj["type"] == kind]
        if not objects:
            continue
        lines.extend([f"## {title}", ""])
        for obj in objects:
            refs = object_refs[obj["id"]]
            citations = "、".join(f"[E{used[ref]}](#e{used[ref]})" for ref in refs)
            lines.extend([f"### {plain(obj['label'])}", "", plain(obj["summary"]), "", f"证据：{citations}。", ""])
    lines.extend(["## 证据索引", "", "同一结论可能引用多段源码；下面保留文档与代码的来源类型。", ""])
    for ref, number in used.items():
        value = evidence[ref]
        lines.extend([f"### E{number}", "", f"{source_label(value.get('source', {}), source_root)} · {plain(value.get('sourceType', 'unknown'))} · `{ref}`", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    report = render(args.bundle, args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(json.dumps({"bundle": str(args.bundle), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
