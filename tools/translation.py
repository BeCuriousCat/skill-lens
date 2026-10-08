"""Validate and attach optional machine translations to explanation reports.

Translations are a reader aid, never a replacement for source Evidence.  The
sidecar is deliberately separate from the immutable Bundle so a translated
report can be regenerated without changing the analysis hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


SCHEMA = "skill-lens.translation.v0.1"
STATUSES = {"machine-translated", "human-reviewed", "unavailable"}


def _entry(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"translation {label} must be an object")
    status = value.get("status", "machine-translated")
    if status not in STATUSES:
        raise ValueError(f"unsupported translation status for {label}: {status}")
    text = value.get("translation", "")
    if status != "unavailable" and (not isinstance(text, str) or not text.strip()):
        raise ValueError(f"translation text missing for {label}")
    if text and not isinstance(text, str):
        raise ValueError(f"translation text must be a string for {label}")
    return {"translation": text, "status": status}


def load(path, graph, claims=None, selected=None):
    """Load a graph-bound sidecar, returning a safe report metadata object.

    ``claims`` and ``selected`` are the IDs currently being rendered. Unknown
    IDs are rejected so a translation cannot silently drift to another graph.
    """
    raw_graph = (Path(graph) / "evidence-graph.json").read_bytes()
    graph_hash = hashlib.sha256(raw_graph).hexdigest()
    graph_value = json.loads(raw_graph)
    graph_node_ids = {node.get("id") for node in graph_value.get("nodes", [])}
    if path is None:
        return {
            "schema": SCHEMA,
            "locale": "zh-CN",
            "mode": "bilingual",
            "source": "model-sidecar",
            "status": "unavailable",
            "message": "未提供逐条中文翻译；报告只显示由规则模板生成的保守中文阶段说明，英文原文仍是唯一证据。",
            "graphSha256": graph_hash,
            "terms": [],
            "claims": {},
            "sources": {},
        }
    sidecar_path = Path(path)
    value = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if value.get("schema") != SCHEMA:
        raise ValueError("translation schema mismatch")
    if value.get("graphSha256") != graph_hash:
        raise ValueError("translation graph hash mismatch")
    if value.get("locale", "zh-CN") != "zh-CN":
        raise ValueError("translation locale must be zh-CN")
    if value.get("mode", "bilingual") != "bilingual":
        raise ValueError("translation mode must be bilingual")
    claim_ids = set(claims or ())
    source_ids = set(selected or ())
    translated_claims = {}
    for key, item in (value.get("claims") or {}).items():
        if key not in claim_ids:
            raise ValueError(f"translation references unknown claim: {key}")
        translated_claims[key] = _entry(item, f"claim {key}")
    translated_sources = {}
    for key, item in (value.get("sources") or {}).items():
        if key not in source_ids:
            raise ValueError(f"translation references unknown source node: {key}")
        translated_sources[key] = _entry(item, f"source {key}")
    overview = value.get("overview") or {}
    if not isinstance(overview, dict):
        raise ValueError("translation overview must be an object")
    for key in ("summary", "confidenceNote"):
        if key in overview and (not isinstance(overview[key], str) or not overview[key].strip()):
            raise ValueError(f"translation overview {key} must be a non-empty string")
    chain = overview.get("mechanismChain", [])
    if not isinstance(chain, list) or any(not isinstance(item, str) or not item.strip() for item in chain):
        raise ValueError("translation overview mechanismChain must be a list of strings")
    modules = overview.get("modules", [])
    if not isinstance(modules, list):
        raise ValueError("translation overview modules must be a list")
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise ValueError(f"translation overview module {index} must be an object")
        for key in ("id", "title", "action", "check"):
            if not isinstance(module.get(key), str) or not module[key].strip():
                raise ValueError(f"translation overview module {index} missing {key}")
        evidence_ids = module.get("evidenceNodeIds", [])
        if not isinstance(evidence_ids, list) or any(node not in source_ids for node in evidence_ids):
            raise ValueError(f"translation overview module {index} references unknown source node")
        labels = module.get("evidenceLabels", [])
        if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise ValueError(f"translation overview module {index} has invalid evidence labels")
    example = overview.get("example")
    if example is not None:
        if not isinstance(example, dict):
            raise ValueError("translation overview example must be an object")
        for key in ("input", "decision", "output", "limitation"):
            if not isinstance(example.get(key), str) or not example[key].strip():
                raise ValueError(f"translation overview example missing {key}")
        evidence_ids = example.get("evidenceNodeIds", [])
        if not isinstance(evidence_ids, list) or any(node not in graph_node_ids for node in evidence_ids):
            raise ValueError("translation overview example references unknown source node")
        labels = example.get("evidenceLabels", [])
        if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise ValueError("translation overview example has invalid evidence labels")
    scenario_presets = overview.get("scenarioPresets", [])
    if not isinstance(scenario_presets, list):
        raise ValueError("translation overview scenarioPresets must be a list")
    for index, preset in enumerate(scenario_presets):
        if not isinstance(preset, dict):
            raise ValueError(f"translation overview scenario preset {index} must be an object")
        for key in ("id", "title", "when", "result"):
            if not isinstance(preset.get(key), str) or not preset[key].strip():
                raise ValueError(f"translation overview scenario preset {index} missing {key}")
        dials = preset.get("dials")
        if not isinstance(dials, dict) or set(dials) != {"DESIGN_VARIANCE", "MOTION_INTENSITY", "VISUAL_DENSITY"}:
            raise ValueError(f"translation overview scenario preset {index} must define the three design dials")
        if any(not isinstance(value, (int, float, str)) or (isinstance(value, str) and not value.strip()) for value in dials.values()):
            raise ValueError(f"translation overview scenario preset {index} has invalid dial values")
        evidence_ids = preset.get("evidenceNodeIds", [])
        if not isinstance(evidence_ids, list) or any(node not in graph_node_ids for node in evidence_ids):
            raise ValueError(f"translation overview scenario preset {index} references unknown source node")
        labels = preset.get("evidenceLabels", [])
        if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise ValueError(f"translation overview scenario preset {index} has invalid evidence labels")
    preset_assessment = overview.get("presetAssessment")
    if preset_assessment is not None:
        if not isinstance(preset_assessment, dict):
            raise ValueError("translation overview presetAssessment must be an object")
        if preset_assessment.get("status") not in {"unobserved", "observed", "partial"}:
            raise ValueError("translation overview presetAssessment has invalid status")
        for key in ("label", "note"):
            if not isinstance(preset_assessment.get(key), str) or not preset_assessment[key].strip():
                raise ValueError(f"translation overview presetAssessment missing {key}")
        evidence_ids = preset_assessment.get("evidenceNodeIds", [])
        if not isinstance(evidence_ids, list) or any(node not in graph_node_ids for node in evidence_ids):
            raise ValueError("translation overview presetAssessment references unknown source node")
        labels = preset_assessment.get("evidenceLabels", [])
        if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise ValueError("translation overview presetAssessment has invalid evidence labels")
    safe_overview = {
        key: overview[key] for key in ("summary", "confidenceNote", "mechanismChain", "modules", "scenarioPresets", "presetAssessment", "example")
        if key in overview
    }
    terms = []
    for index, item in enumerate(value.get("terms") or []):
        if not isinstance(item, dict) or not item.get("source") or not item.get("translation"):
            raise ValueError(f"invalid translation term at index {index}")
        terms.append({"source": str(item["source"]), "translation": str(item["translation"])})
    status = value.get("status", "partial")
    if status not in {"available", "partial", "unavailable"}:
        raise ValueError("unsupported translation sidecar status")
    return {
        "schema": SCHEMA,
        "locale": "zh-CN",
        "mode": "bilingual",
        "source": value.get("source", "model-sidecar"),
        "status": status,
        "message": value.get("message", "英文原文保留为证据，中文内容是阅读辅助。"),
        "graphSha256": graph_hash,
        "terms": terms,
        "claims": translated_claims,
        "sources": translated_sources,
        "overview": safe_overview,
        "path": str(sidecar_path.resolve()),
    }


def attach(value, translation):
    """Attach translation metadata while preserving exact source fields."""
    result = dict(value)
    result["translation"] = {k: v for k, v in translation.items() if k != "path"}
    result["translation"].pop("claims", None)
    result["translation"].pop("sources", None)
    result["translation"].pop("graphSha256", None)
    result["translation"]["coverage"] = {
        "claims": len(translation.get("claims", {})),
        "sources": len(translation.get("sources", {})),
    }
    if translation.get("overview"):
        result["translation"]["overview"] = translation["overview"]
    result["claims"] = []
    for claim in value["claims"]:
        item = dict(claim)
        if claim["id"] in translation.get("claims", {}):
            item["translation"] = translation["claims"][claim["id"]]["translation"]
            item["translationStatus"] = translation["claims"][claim["id"]]["status"]
        result["claims"].append(item)
    investigation = dict(value["investigation"])
    investigation["selected"] = []
    for selected in value["investigation"]["selected"]:
        item = dict(selected)
        if selected["nodeId"] in translation.get("sources", {}):
            item["translation"] = translation["sources"][selected["nodeId"]]["translation"]
            item["translationStatus"] = translation["sources"][selected["nodeId"]]["status"]
        investigation["selected"].append(item)
    result["investigation"] = investigation
    return result
