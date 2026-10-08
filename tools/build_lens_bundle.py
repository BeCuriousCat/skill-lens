#!/usr/bin/env python3
"""Assemble and validate an offline Lens Bundle from verified artifacts."""

import argparse
import json
from pathlib import Path

from validate_semantic_projection import validate_projection
from write_lens_bundle import write_bundle


def load(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--diagnostics", required=True, type=Path)
    parser.add_argument("--sources-lock", required=True, type=Path)
    parser.add_argument("--analysis-manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    graph = load(args.graph)
    projection = load(args.projection)
    validation = validate_projection(projection, graph)
    if not validation["valid"]:
        raise SystemExit("invalid semantic projection: " + "; ".join(validation["errors"]))
    names = {
        "artifact": "artifact.json",
        "graph": "evidence-graph.json",
        "projection": "semantic-projection.json",
        "diagnostics": "diagnostics.json",
        "sources_lock": "sources.lock.json",
        "analysis_manifest": "analysis-manifest.json",
    }
    inputs = {key: load(path) for key, path in (("artifact", args.artifact), ("graph", args.graph), ("projection", args.projection), ("diagnostics", args.diagnostics), ("sources_lock", args.sources_lock), ("analysis_manifest", args.analysis_manifest))}
    write_bundle(args.out, {filename: inputs[key] for key, filename in names.items()})
    print(json.dumps({"bundle": str(args.out), "caseId": graph.get("caseId"), "revision": graph.get("revision"), "files": sorted(names.values())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
