#!/usr/bin/env python3
"""Evaluate a projection against question boundaries and an Evidence Graph.

This is a deterministic proxy evaluation. It does not replace human review or
measure prose quality; it checks evidence references, literal required phrases,
and forbidden claims in the projection text.
"""

import argparse
import json
import re
from pathlib import Path

from validate_semantic_projection import validate_projection


def load(path):
    return json.loads(Path(path).read_text())


def forbidden_claim_present(text, phrase):
    """Avoid flagging an explicitly negated boundary as a positive claim."""
    start = 0
    phrase = phrase.casefold()
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return False
        sentence_start = max(text.rfind(mark, 0, index) for mark in (".", "?", "!")) + 1
        context = text[sentence_start:index]
        # Models often express a boundary indirectly ("nothing in the
        # evidence shows that X"), so a literal search for "not X" is not
        # sufficient to distinguish a limitation from a positive claim.
        indirect_negation = re.search(
            r"\b(?:nothing|no evidence|the evidence)\b.{0,120}\b(?:shows?|does not|doesn't|cannot|can't|cannot establish|fails to|doesn't show|does not show|not show|not establish|not prove|not demonstrate)\b",
            context,
            flags=re.IGNORECASE,
        )
        if not any(marker in context for marker in ("not ", "does not ", "cannot ", "unknown", "no ")) and not indirect_negation:
            return True
        start = index + len(phrase)


def evaluate(projection, graph, questions, gold_projection=None):
    validation = validate_projection(projection, graph)
    # Report the failed gate even for JSON values with invalid primitive shapes.
    projection = projection if isinstance(projection, dict) else {}
    graph = graph if isinstance(graph, dict) else {}
    raw_nodes = graph.get("nodes")
    node_ids = {node["id"] for node in (raw_nodes if isinstance(raw_nodes, list) else []) if isinstance(node, dict) and isinstance(node.get("id"), str)}
    raw_objects = projection.get("objects")
    objects = [obj for obj in raw_objects if isinstance(obj, dict)] if isinstance(raw_objects, list) else []
    text = " ".join(f"{obj.get('label', '')} {obj.get('summary', '')}" for obj in objects).casefold()
    reference_errors = []
    for obj in objects:
        refs = obj.get("evidenceNodeIds")
        for node_id in refs if isinstance(refs, list) else []:
            if not isinstance(node_id, str) or node_id not in node_ids:
                reference_errors.append({"objectId": obj.get("id"), "nodeId": node_id})
    question_rows = []
    for question in questions:
        required = question.get("mustInclude", [])
        forbidden = question.get("mustNotClaim", [])
        matched = [phrase for phrase in required if phrase.casefold() in text]
        violations = [phrase for phrase in forbidden if forbidden_claim_present(text, phrase)]
        question_rows.append({
            "questionId": question["id"],
            "requiredPhraseCount": len(required),
            "matchedPhraseCount": len(matched),
            "literalMustIncludeCoverage": len(matched) / len(required) if required else 1.0,
            "matchedMustInclude": matched,
            "mustNotClaimViolations": violations,
        })
    total_required = sum(row["requiredPhraseCount"] for row in question_rows)
    total_matched = sum(row["matchedPhraseCount"] for row in question_rows)
    total_violations = sum(len(row["mustNotClaimViolations"]) for row in question_rows)
    gold_metrics = None
    if gold_projection is not None:
        def refs(obj):
            raw = obj.get("evidenceNodeIds")
            return {ref for ref in raw if isinstance(ref, str)} if isinstance(raw, list) else set()

        candidate_objects = objects
        gold_objects = gold_projection.get("objects", [])
        graph_run = graph.get("analysisRun")
        gold_run = gold_projection.get("analysisRun")
        comparable = graph_run is not None and gold_run is not None and graph_run == gold_run
        covered_objects = 0
        if comparable:
            for gold_obj in gold_objects:
                gold_refs = refs(gold_obj)
                if any(
                    candidate.get("type") == gold_obj.get("type")
                    and gold_refs.intersection(refs(candidate))
                    for candidate in candidate_objects
                ):
                    covered_objects += 1
        gold_evidence = {ref for obj in gold_objects for ref in refs(obj)}
        candidate_evidence = {ref for obj in candidate_objects for ref in refs(obj)}
        gold_metrics = {
            "goldComparisonComparable": comparable,
            "candidateAnalysisRun": graph_run,
            "goldAnalysisRun": gold_run,
            "goldObjectCount": len(gold_objects),
            "goldObjectCoverage": covered_objects / len(gold_objects) if gold_objects and comparable else None,
            "goldEvidenceNodeRecall": len(gold_evidence.intersection(candidate_evidence)) / len(gold_evidence) if gold_evidence and comparable else None,
            "goldEvidenceNodeCount": len(gold_evidence),
        }
    return {
        "schema": "skill-lens.semantic-projection-evaluation.v0.1",
        "caseId": projection.get("caseId"),
        "revision": projection.get("revision"),
        "objectCount": len(objects),
        "evidenceReferenceErrors": reference_errors,
        "evidenceValidationErrors": validation["errors"],
        "evidenceGatePass": validation["valid"],
        "questionCount": len(question_rows),
        "literalMustIncludeCoverage": total_matched / total_required if total_required else 1.0,
        "mustNotClaimViolationCount": total_violations,
        "questionResults": question_rows,
        "goldComparison": gold_metrics,
        "interpretation": "deterministic proxy; literal phrase coverage and boundary checks do not establish semantic answer quality",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("projection", type=Path)
    parser.add_argument("graph", type=Path)
    parser.add_argument("questions", type=Path)
    parser.add_argument("--gold-projection", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(load(args.projection), load(args.graph), load(args.questions)["questions"], load(args.gold_projection) if args.gold_projection else None)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload)
    summary = {key: result[key] for key in ("caseId", "objectCount", "evidenceGatePass", "literalMustIncludeCoverage", "mustNotClaimViolationCount")}
    if result["goldComparison"]:
        summary.update(result["goldComparison"])
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
