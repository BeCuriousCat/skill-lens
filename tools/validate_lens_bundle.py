#!/usr/bin/env python3
"""Validate an offline Lens Bundle and flag likely credential fields."""

import argparse
import json
import re
from pathlib import Path

from validate_semantic_projection import validate_projection
from instruction_analysis import load_analysis

REQUIRED = ("artifact.json", "evidence-graph.json", "semantic-projection.json", "diagnostics.json", "sources.lock.json", "analysis-manifest.json")
REQUIRED_SCHEMAS = {
    "artifact.json": {"skill-lens.artifact.v0.1"},
    # Provider smoke fixtures may carry the accepted GraphPatch directly.
    "evidence-graph.json": {"skill-lens.evidence-graph.v0.1", "skill-lens.graph-patch.v0.1"},
    "semantic-projection.json": {"skill-lens.semantic-projection.v0.1"},
    "diagnostics.json": {"skill-lens.diagnostics.v0.1"},
    # Older source locks predate a schema field; structural identity is checked below.
    "sources.lock.json": {"skill-lens.source-lock.v1", None},
    "analysis-manifest.json": {"skill-lens.analysis-manifest.v0.1", "skill-lens.provider-run-manifest.v0.1"},
}
MANIFEST_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "skill-lens.analysis-manifest.v0.1.schema.json"
SECRET_KEYS = re.compile(r"(api[_-]?key|access[_-]?token|secret|authorization|cookie)", re.I)
PLACEHOLDER = re.compile(r"^(?:$|none|null|your[_ -]?api[_ -]?key|<[^>]+>|\[redacted\]|\*+)$", re.I)


def find_secret_fields(value, path=""):
    findings = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if SECRET_KEYS.search(key) and isinstance(child, str) and not PLACEHOLDER.match(child.strip()):
                findings.append(child_path)
            findings.extend(find_secret_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_secret_fields(child, f"{path}[{index}]"))
    return findings


def validate(bundle):
    bundle = Path(bundle)
    errors = []
    values = {}
    for name in REQUIRED:
        path = bundle / name
        if not path.is_file():
            errors.append(f"missing {name}")
            continue
        try:
            values[name] = json.loads(path.read_text())
        except Exception as error:
            errors.append(f"invalid JSON {name}: {error}")
    graph = values.get("evidence-graph.json", {})
    projection = values.get("semantic-projection.json", {})
    for name, schemas in REQUIRED_SCHEMAS.items():
        if name in values and values[name].get("schema") not in schemas:
            errors.append(f"unexpected schema in {name}: expected one of {sorted(str(item) for item in schemas)}")
    manifest = values.get("analysis-manifest.json")
    if manifest and manifest.get("schema") == "skill-lens.analysis-manifest.v0.1":
        # Historical smoke manifests used this schema name before provider
        # selection metadata existed. Validate the stronger contract only for
        # manifests emitted by the current CLI (identified by requestedProviders).
        if "requestedProviders" in manifest:
            try:
                import jsonschema
                schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
                errors.extend(f"analysis-manifest: {error.message}" for error in jsonschema.Draft202012Validator(schema).iter_errors(manifest))
            except ImportError:
                required = {"schema", "caseId", "revision", "provider", "requestedProviders", "completedProviders", "status"}
                if not required.issubset(manifest):
                    errors.append("analysis-manifest: missing required fields")
        if "requestedProviders" in manifest and isinstance(manifest.get("plannedProviders"), list) and manifest.get("selectionMode") == "auto":
            requested = manifest.get("requestedProviders", [])
            has_facet_gap = any(item.get("code") == "unsupported-facet-coverage-gap" for item in values.get("diagnostics.json", {}).get("diagnostics", []))
            if requested != manifest.get("plannedProviders") and not manifest.get("fallbackReason") and not has_facet_gap:
                errors.append("analysis-manifest: auto requestedProviders must equal plannedProviders unless fallback is recorded")
    if graph and projection:
        result = validate_projection(projection, graph)
        errors.extend(f"projection: {error}" for error in result["errors"])
    revision = graph.get("revision")
    for name in ("artifact.json", "diagnostics.json", "sources.lock.json", "analysis-manifest.json"):
        if name in values and revision and values[name].get("revision") not in (None, revision):
            errors.append(f"revision mismatch in {name}")
    if graph:
        for name in ("artifact.json", "diagnostics.json", "sources.lock.json", "analysis-manifest.json"):
            if name in values and values[name].get("caseId") not in (None, graph.get("caseId")):
                errors.append(f"caseId mismatch in {name}")
    if (bundle / 'instruction-analysis.json').is_file():
        try:
            values['instruction-analysis.json'] = load_analysis(bundle)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f'instruction-analysis: {error}')
    secrets = sorted({finding for value in values.values() for finding in find_secret_fields(value)})
    if secrets:
        errors.append("possible credential fields: " + ", ".join(secrets))
    return {"valid": not errors, "bundle": str(bundle), "caseId": graph.get("caseId"), "revision": revision, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    result = validate(args.bundle)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
