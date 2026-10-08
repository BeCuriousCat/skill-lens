#!/usr/bin/env python3
"""Apply an externally produced Semantic Projection to a Lens Bundle safely."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_lens_bundle import validate as validate_bundle
from validate_semantic_projection import validate_projection
from write_lens_bundle import write_bundle


def apply(bundle: Path, projection_path: Path) -> dict:
    graph_path = bundle / "evidence-graph.json"
    manifest_path = bundle / "analysis-manifest.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection_result = validate_projection(projection, graph)
    if not projection_result["valid"]:
        raise ValueError("projection failed Evidence Gate: " + "; ".join(projection_result["errors"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["projection"] = "external-file-v0.1"
    manifest["projectionSource"] = projection_path.name
    values = {name: json.loads((bundle / name).read_text(encoding="utf-8"))
              for name in ("artifact.json", "evidence-graph.json", "semantic-projection.json", "diagnostics.json", "sources.lock.json", "analysis-manifest.json")}
    values["semantic-projection.json"] = projection
    values["analysis-manifest.json"] = manifest
    # write_bundle stages and validates all six documents, then swaps the
    # directory. A failure leaves the published Bundle untouched.
    write_bundle(bundle, values)
    result = validate_bundle(bundle)
    if not result["valid"]:
        raise ValueError("Bundle validation failed after projection apply: " + "; ".join(result["errors"]))
    return {"bundle": str(bundle), "caseId": projection["caseId"], "revision": projection["revision"], "objectCount": len(projection["objects"]), "projection": manifest["projection"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("projection", type=Path)
    args = parser.parse_args()
    result = apply(args.bundle, args.projection)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
