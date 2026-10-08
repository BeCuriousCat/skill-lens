#!/usr/bin/env python3
"""Run an external Semantic Projector through a strict JSON stdin/stdout protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from evaluate_semantic_projection import evaluate
from validate_evidence_slices import validate_file as validate_evidence_slices_file
from validate_semantic_projection import validate_projection
from validate_question_slices import validate
from projector_request_compaction import CODEC, compact_request, canonical_hash, inherited_field_count, pooled_source_count, pooled_claim_count


REQUEST_SCHEMA = "skill-lens.semantic-projector-request.v0.1"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def publish_pair(first_path: Path, first_data: bytes, second_path: Path, second_data: bytes):
    """Stage both records before replacing either published output."""
    targets = [(first_path, first_data), (second_path, second_data)]
    staged = []
    backups = []
    try:
        for target, data in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            staged.append((target, Path(temporary)))
        for target, _ in staged:
            if target.exists():
                fd, backup = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".bak", dir=target.parent)
                os.close(fd)
                backup_path = Path(backup)
                backup_path.write_bytes(target.read_bytes())
                backups.append((target, backup_path))
        replaced = []
        try:
            for target, temporary in staged:
                os.replace(temporary, target)
                replaced.append(target)
        except OSError:
            for target in reversed(replaced):
                backup = next((path for saved_target, path in backups if saved_target == target), None)
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
            raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)
        for _, backup in backups:
            backup.unlink(missing_ok=True)


ALLOWED_PROVENANCE_SOURCES = {"model", "external-program", "fixture"}


def build_request(graph: dict, questions: list[dict], slices: list[dict] | None = None) -> dict:
    """Keep evaluator-only answers private and restrict sliced requests to selected facts."""
    public_questions = []
    for question in questions:
        prompt = question.get("question", question.get("prompt"))
        if not isinstance(question.get("id"), str) or not isinstance(prompt, str) or not prompt:
            raise ValueError("Projector questions require an id and question text")
        public_questions.append({"id": question["id"], "question": prompt})
    request_graph = graph
    public_slices = None
    if slices is not None:
        pairs = {
            (item["nodeId"], item["evidenceId"])
            for record in slices for item in record["slice"].get("selected", [])
        }
        node_ids = {node_id for node_id, _ in pairs}
        evidence_ids = {evidence_id for _, evidence_id in pairs}
        nodes = [
            {**{key: node[key] for key in ("id", "type", "label", "attributes") if key in node},
             "evidence": [ref for ref in node.get("evidence", []) if (node["id"], ref) in pairs]}
            for node in graph.get("nodes", []) if node["id"] in node_ids
        ]
        edges = [
            edge for edge in graph.get("edges", [])
            if edge.get("from") in node_ids and edge.get("to") in node_ids
            and all(ref in evidence_ids for ref in edge.get("evidence", []))
        ]
        request_graph = {
            key: graph[key] for key in ("schema", "caseId", "revision", "analysisRun", "provider") if key in graph
        }
        request_graph.update({"nodes": nodes, "edges": edges,
                              "evidence": [ref for ref in graph.get("evidence", []) if ref["id"] in evidence_ids]})
        # Forward only selection identities. The canonical facts and source
        # quotes already appear in request_graph; arbitrary slice fields could
        # carry evaluator notes or a second unbounded context.
        public_slices = [
            {"questionId": record["questionId"], "slice": {"selected": [
                {"nodeId": item["nodeId"], "evidenceId": item["evidenceId"]}
                for item in record["slice"].get("selected", [])
            ]}}
            for record in slices
        ]
    request = {"schema": REQUEST_SCHEMA, "caseId": graph["caseId"], "revision": graph["revision"],
               "graph": request_graph, "questions": public_questions}
    if public_slices is not None:
        request["questionSlices"] = public_slices
    return request


def run(program: Path, arguments: list[str], graph: dict, questions: list[dict], timeout: float, slices: list[dict] | None = None, provenance_source: str = "external-program", slice_payload: dict | None = None, request_compaction: str = "none") -> tuple[dict, dict]:
    if provenance_source not in ALLOWED_PROVENANCE_SOURCES:
        raise ValueError(f"unsupported provenance source: {provenance_source}")
    if slices is not None:
        if slice_payload and slice_payload.get("schema") == "skill-lens.evidence-question-slices.v0.1":
            slice_validation = validate_evidence_slices_file(slice_payload, graph)
        else:
            slice_validation = validate(slices, graph)
        if not slice_validation["valid"]:
            raise ValueError("question slices failed GraphPatch validation: " + "; ".join(slice_validation["errors"]))
    request = build_request(graph, questions, slices)
    original_request_text = json.dumps(request, ensure_ascii=False)
    if request_compaction not in {"none", CODEC}:
        raise ValueError("unsupported request compaction")
    wire_request = compact_request(request) if request_compaction == CODEC else request
    request_text = (json.dumps(wire_request, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    if request_compaction == CODEC else original_request_text)
    started = time.perf_counter()
    completed = subprocess.run([str(program), *arguments], input=request_text, capture_output=True, text=True, timeout=timeout, check=False)
    elapsed = round(time.perf_counter() - started, 6)
    if completed.returncode:
        raise RuntimeError(f"external projector exited {completed.returncode}: {completed.stderr.strip()[:500]}")
    try:
        projection = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"external projector stdout is not JSON: {error}") from error
    initial_validation = validate_projection(projection, request["graph"])
    if not initial_validation["valid"]:
        raise ValueError("external Projection failed Evidence Gate: " + "; ".join(initial_validation["errors"]))
    provenance = {
        "runId": str(uuid.uuid4()),
        "source": provenance_source,
        "generatedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # The runner owns the run identity so the Projection and sidecar cannot
    # silently describe different executions. The caller chooses `model`
    # explicitly when an actual model adapter is used.
    projection["provenance"] = provenance
    validation = validate_projection(projection, graph)
    if not validation["valid"]:
        raise ValueError("external Projection failed Evidence Gate: " + "; ".join(validation["errors"]))
    metadata = {
        "schema": "skill-lens.semantic-projector-run.v0.1",
        "caseId": graph["caseId"], "revision": graph["revision"],
        "program": str(program), "arguments": arguments, "timeoutSeconds": timeout,
        "runtimeSeconds": elapsed, "stdoutSha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "provenance": provenance,
        "stderrPresent": bool(completed.stderr), "questionSliceCount": len(slices or []), "evidenceGate": validation,
        "requestIsolation": {
            "policy": "questions-only-selected-facts-v0.1",
            "questionFields": ["id", "question"],
            "graphMode": "selected-subgraph" if slices is not None else "full-graph",
            "sourceGraphNodeCount": len(graph.get("nodes", [])),
            "requestGraphNodeCount": len(request["graph"].get("nodes", [])),
            "requestGraphEvidenceCount": len(request["graph"].get("evidence", [])),
            "requestChars": len(request_text),
            "requestSha256": hashlib.sha256(request_text.encode("utf-8")).hexdigest(),
        },
    }
    if request_compaction == CODEC:
        metadata["requestCompaction"] = {
            "codec": CODEC,
            "originalRequestChars": len(original_request_text),
            "wireRequestChars": len(request_text),
            "expandedRequestCanonicalSha256": canonical_hash(request),
            "inheritedClaimFieldCount": inherited_field_count(wire_request),
            "pooledSourceReferenceCount": pooled_source_count(wire_request),
            "pooledClaimReferenceCount": pooled_claim_count(wire_request),
            "roundTripVerified": True,
        }
    return projection, metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--question-slices", type=Path)
    parser.add_argument("--gold-projection", type=Path)
    parser.add_argument("--provenance-source", choices=sorted(ALLOWED_PROVENANCE_SOURCES), default="external-program", help="source kind recorded in the Projection and run sidecar")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--request-compaction", choices=("none", CODEC), default="none",
                        help="Experimental lossless shared source/claim request format; requires a compatible adapter")
    parser.add_argument("--projection-out", type=Path, required=True)
    parser.add_argument("--run-out", type=Path, required=True)
    args = parser.parse_args()
    graph = read(args.graph); questions = read(args.questions)["questions"]
    slice_payload = read(args.question_slices) if args.question_slices else None
    slices = slice_payload["slices"] if slice_payload else None
    projection, metadata = run(args.program, args.arg, graph, questions, args.timeout_seconds, slices, args.provenance_source, slice_payload, args.request_compaction)
    evaluation = evaluate(projection, graph, questions, read(args.gold_projection) if args.gold_projection else None)
    metadata["evaluation"] = evaluation
    projection_bytes = (json.dumps(projection, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    run_bytes = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    publish_pair(args.projection_out, projection_bytes, args.run_out, run_bytes)
    print(json.dumps({"caseId": graph["caseId"], "projection": str(args.projection_out), "evidenceGatePass": evaluation["evidenceGatePass"], "runtimeSeconds": metadata["runtimeSeconds"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"projection command failed: {error}") from error
