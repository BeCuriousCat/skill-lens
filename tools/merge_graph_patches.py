"""Combine independent Provider facts without discarding claims or their origin."""

from __future__ import annotations

import copy
import hashlib


def _qualified(kind, provider, original):
    digest = hashlib.sha1(f"{kind}:{provider}:{original}".encode()).hexdigest()[:16]
    return f"{kind}:{digest}"


def _origin(item, provider):
    extensions = item.setdefault("extensions", {})
    original = copy.deepcopy(extensions)
    extensions["skillLensProviders"] = [provider]
    extensions["skillLensClaims"] = {provider: {"attributes": copy.deepcopy(item.get("attributes", {})),
        "evidence": list(item.get("evidence", [])), "extensions": original}}


def _compatible(left, right, kind):
    keys = ("type", "label") if kind == "nodes" else (("from", "to", "type") if kind == "edges" else ("sourceType", "source", "quote", "confidence"))
    if any(left.get(key) != right.get(key) for key in keys):
        return False
    return all(key not in left.get("attributes", {}) or key not in right.get("attributes", {}) or left["attributes"][key] == right["attributes"][key]
               for key in left.get("attributes", {}).keys() | right.get("attributes", {}).keys())


def merge(patches):
    if not patches:
        raise ValueError("at least one GraphPatch is required")
    case_id, revision = patches[0]["caseId"], patches[0]["revision"]
    if any(patch["caseId"] != case_id or patch["revision"] != revision for patch in patches):
        raise ValueError("GraphPatch caseId/revision mismatch")
    providers = [patch["provider"] for patch in patches]
    if len({item["id"] for item in providers}) != len(providers):
        raise ValueError("duplicate Provider in merge")
    result = {"schema": "skill-lens.graph-patch.v0.1", "provider": {"id": "skill-lens-composite", "version": "0.1.0", "members": providers},
              "caseId": case_id, "revision": revision, "nodes": [], "edges": [], "evidence": [], "diagnostics": []}
    indexes = {kind: {} for kind in ("nodes", "edges", "evidence")}

    for patch in patches:
        provider = patch["provider"]["id"]
        maps = {kind: {} for kind in indexes}
        for kind in ("evidence", "nodes", "edges"):
            for original in patch[kind]:
                item = copy.deepcopy(original)
                if kind == "nodes":
                    item["evidence"] = [maps["evidence"][ref] for ref in item["evidence"]]
                elif kind == "edges":
                    item["from"] = maps["nodes"][item["from"]]
                    item["to"] = maps["nodes"][item["to"]]
                    item["evidence"] = [maps["evidence"][ref] for ref in item.get("evidence", [])]
                existing = indexes[kind].get(item["id"])
                if existing is not None and _compatible(existing, item, kind):
                    claim = {"attributes": copy.deepcopy(item.get("attributes", {})),
                             "evidence": list(item.get("evidence", [])), "extensions": copy.deepcopy(item.get("extensions", {}))}
                    if kind != "evidence":
                        existing.setdefault("attributes", {}).update(item.get("attributes", {}))
                    for ref in item.get("evidence", []):
                        if ref not in existing.setdefault("evidence", []):
                            existing["evidence"].append(ref)
                    existing["extensions"]["skillLensProviders"].append(provider)
                    existing["extensions"]["skillLensClaims"][provider] = claim
                    maps[kind][original["id"]] = existing["id"]
                    continue
                if existing is not None:
                    old_id = item["id"]
                    item["id"] = _qualified(kind[:-1], provider, old_id)
                    if item["id"] in indexes[kind]:
                        raise ValueError(f"qualified {kind} ID collision: {item['id']}")
                    group = _qualified("conflict", kind, old_id)
                    existing.setdefault("extensions", {})["conflictGroup"] = group
                    item.setdefault("extensions", {})["conflictGroup"] = group
                    result["diagnostics"].append({"code": "provider-claim-conflict", "severity": "warning",
                        "message": f"Providers emitted different {kind} with ID {old_id}",
                        "nodeIds" if kind == "nodes" else "edgeIds" if kind == "edges" else "extensions":
                            [existing["id"], item["id"]] if kind != "evidence" else {"evidenceIds": [existing["id"], item["id"]]}})
                _origin(item, provider)
                result[kind].append(item)
                indexes[kind][item["id"]] = item
                maps[kind][original["id"]] = item["id"]
        for diagnostic in patch["diagnostics"]:
            item = copy.deepcopy(diagnostic)
            item["nodeIds"] = [maps["nodes"][ref] for ref in item.get("nodeIds", [])]
            item["edgeIds"] = [maps["edges"][ref] for ref in item.get("edgeIds", [])]
            item.setdefault("extensions", {})["skillLensProvider"] = provider
            result["diagnostics"].append(item)
    return result
