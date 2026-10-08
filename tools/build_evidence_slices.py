#!/usr/bin/env python3
"""Build bounded Evidence Graph slices for every benchmark question."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from slice_evidence_graph import slice_graph


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-chars", type=int, default=6000)
    parser.add_argument("--max-nodes", type=int, default=24)
    parser.add_argument('--context-mode', choices=('minimal', 'source-companions', 'document-sections'), default='minimal')
    parser.add_argument('--context-budget-chars', type=int, default=0)
    args = parser.parse_args()
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    questions = json.loads(args.questions.read_text(encoding="utf-8"))["questions"]
    slices = [
        {"questionId": question["id"], "question": question["question"],
         "slice": slice_graph(graph, question["question"], args.budget_chars, args.max_nodes,
                              args.context_mode, args.context_budget_chars)}
        for question in questions
    ]
    payload = {
        "schema": "skill-lens.evidence-question-slices.v0.1",
        "caseId": graph["caseId"],
        "revision": graph["revision"],
        "graph": str(args.graph),
        "budgetChars": args.budget_chars + args.context_budget_chars,
        "primaryBudgetChars": args.budget_chars,
        "contextBudgetChars": args.context_budget_chars,
        "maxNodes": args.max_nodes,
        "contextMode": args.context_mode,
        "slices": slices,
        "boundary": "Lexical retrieval only; slices do not prove execution or semantic answerability.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"caseId": graph["caseId"], "questionCount": len(slices), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
