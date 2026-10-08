"""Opt-in reversible model request pooling of source metadata and claims.

Canonical facts, Evidence quotes/positions and differing claims remain explicit;
repeated file metadata uses a shared source dictionary.
Recorded inheritance is reversible; this module does not modify Lens Bundles.
"""
from __future__ import annotations

import copy
import hashlib
import json
from itertools import combinations

CODEC = "shared-sources-and-claims-v1"
MARKER = "skillLensCanonicalFields"
FIELDS = ("attributes", "evidence")
SOURCE_MARKER = "skillLensSourceRef"
SOURCE_FIELDS = ("repository", "revision", "path")
SOURCE_TABLE = "requestSources"
CLAIM_MARKER = "skillLensClaimRef"
CLAIM_TABLE = "requestClaims"


def canonical_json(value) -> str:
    # Comparing JSON syntax also distinguishes True from 1, unlike Python ==.
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def canonical_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _claims(request):
    graph = request.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("edges"), list):
        raise ValueError("request compaction requires a graph with an edge array")
    for edge in graph["edges"]:
        if not isinstance(edge, dict):
            raise ValueError("request compaction requires object edges")
        extensions = edge.get("extensions", {})
        if not isinstance(extensions, dict):
            continue
        claims = extensions.get("skillLensClaims", {})
        if not isinstance(claims, dict):
            continue
        for claim in claims.values():
            if isinstance(claim, dict):
                yield edge, claim


def compact_request(request: dict) -> dict:
    if (not isinstance(request, dict) or "requestCompaction" in request
            or SOURCE_TABLE in request or CLAIM_TABLE in request):
        raise ValueError("request compaction marker already present or invalid request")
    result = copy.deepcopy(request)
    for edge, claim in _claims(result):
        if MARKER in claim:
            raise ValueError("reserved claim inheritance marker already present")
        eligible = [field for field in FIELDS if field in edge and field in claim
                    and canonical_json(edge[field]) == canonical_json(claim[field])]
        best = claim
        for count in range(1, len(eligible) + 1):
            for fields in combinations(eligible, count):
                candidate = {key: value for key, value in claim.items() if key not in fields}
                candidate[MARKER] = list(fields)
                if len(canonical_json(candidate)) < len(canonical_json(best)):
                    best = candidate
        if best is not claim:
            claim.clear()
            claim.update(best)
    _pool_sources(result)
    _pool_claims(result)
    result["requestCompaction"] = CODEC
    # The check includes unknown fields and preserves array ordering.
    if canonical_json(expand_request(result)) != canonical_json(request):
        raise ValueError("request compaction failed lossless reconstruction")
    return result


def expand_request(request: dict) -> dict:
    if not isinstance(request, dict) or request.get("requestCompaction") != CODEC:
        raise ValueError("unsupported request compaction")
    result = copy.deepcopy(request)
    del result["requestCompaction"]
    sources = result.pop(SOURCE_TABLE, {})
    if not isinstance(sources, dict):
        raise ValueError("invalid shared source dictionary")
    used_sources = set()
    for source in _sources(result):
        if SOURCE_MARKER not in source:
            continue
        source_id = source.pop(SOURCE_MARKER)
        shared = sources.get(source_id) if isinstance(source_id, str) else None
        if (not isinstance(shared, dict) or not {'revision', 'path'} <= set(shared)
                or not set(shared) <= set(SOURCE_FIELDS)
                or any(not isinstance(value, str) or not value for value in shared.values())
                or any(field in source for field in shared)):
            raise ValueError("shared source is missing, malformed or conflicts")
        source.update(copy.deepcopy(shared))
        used_sources.add(source_id)
    if used_sources != set(sources):
        raise ValueError("unreferenced shared source metadata")
    _expand_claims(result)
    for edge, claim in _claims(result):
        if MARKER not in claim:
            continue
        inherited = claim.pop(MARKER)
        if (not isinstance(inherited, list) or not inherited
                or any(not isinstance(field, str) or field not in FIELDS for field in inherited)
                or len(set(inherited)) != len(inherited)):
            raise ValueError("invalid claim inheritance fields")
        for field in inherited:
            if field not in edge or field in claim:
                raise ValueError("claim inheritance is missing its canonical field or conflicts")
            claim[field] = copy.deepcopy(edge[field])
    return result


def inherited_field_count(request: dict) -> int:
    table = request.get(CLAIM_TABLE, {})
    return sum(len((table.get(claim[CLAIM_MARKER], {}) if CLAIM_MARKER in claim else claim).get(MARKER, []))
               for _, claim in _claims(request))


def _sources(request):
    evidence = request.get("graph", {}).get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("request compaction requires an Evidence array")
    for item in evidence:
        if isinstance(item, dict) and isinstance(item.get("source"), dict):
            yield item["source"]


def _pool_sources(request):
    groups = {}
    for source in _sources(request):
        if SOURCE_MARKER in source:
            raise ValueError("reserved source reference marker already present")
        if not all(isinstance(source.get(field), str) and source[field] for field in ('revision', 'path')):
            continue
        shared = {field: source[field] for field in SOURCE_FIELDS
                  if isinstance(source.get(field), str) and source[field]}
        groups.setdefault(canonical_json(shared), (shared, []))[1].append(source)
    table = {}
    for shared, sources in groups.values():
        if len(sources) < 2:
            continue
        source_id = f"source:{len(table)}"
        candidates = [{**{key: value for key, value in source.items() if key not in shared},
                       SOURCE_MARKER: source_id} for source in sources]
        before = sum(len(canonical_json(source)) for source in sources)
        after = (sum(len(canonical_json(candidate)) for candidate in candidates)
                 + len(canonical_json({source_id: shared})) + len(SOURCE_TABLE) + 4)
        if after >= before:
            continue
        table[source_id] = shared
        for source, candidate in zip(sources, candidates):
            source.clear()
            source.update(candidate)
    if table:
        request[SOURCE_TABLE] = table


def pooled_source_count(request: dict) -> int:
    return sum(SOURCE_MARKER in source for source in _sources(request))


def _claim_entries(request):
    for collection in ('nodes', 'edges', 'evidence'):
        for item in request.get('graph', {}).get(collection, []):
            extensions = item.get('extensions', {}) if isinstance(item, dict) else {}
            claims = extensions.get('skillLensClaims', {}) if isinstance(extensions, dict) else {}
            if isinstance(claims, dict):
                for provider, claim in claims.items():
                    if isinstance(claim, dict):
                        yield claims, provider, claim


def _pool_claims(request):
    groups = {}
    for claims, provider, claim in _claim_entries(request):
        if CLAIM_MARKER in claim:
            raise ValueError('reserved claim dictionary reference already present')
        groups.setdefault(canonical_json(claim), (claim, []))[1].append((claims, provider))
    table = {}
    for claim, entries in groups.values():
        if len(entries) < 2:
            continue
        claim_id = f'claim:{len(table)}'
        reference = {CLAIM_MARKER: claim_id}
        before = len(canonical_json(claim)) * len(entries)
        after = (len(canonical_json(reference)) * len(entries)
                 + len(canonical_json({claim_id: claim})) + len(CLAIM_TABLE) + 4)
        if after >= before:
            continue
        table[claim_id] = copy.deepcopy(claim)
        for claims, provider in entries:
            claims[provider] = copy.deepcopy(reference)
    if table:
        request[CLAIM_TABLE] = table


def _expand_claims(request):
    table = request.pop(CLAIM_TABLE, {})
    if not isinstance(table, dict):
        raise ValueError('invalid shared claim dictionary')
    used = set()
    for claims, provider, claim in _claim_entries(request):
        if CLAIM_MARKER not in claim:
            continue
        claim_id = claim.get(CLAIM_MARKER)
        shared = table.get(claim_id) if isinstance(claim_id, str) else None
        if set(claim) != {CLAIM_MARKER} or not isinstance(shared, dict) or CLAIM_MARKER in shared:
            raise ValueError('shared claim is missing, recursive or conflicts')
        claims[provider] = copy.deepcopy(shared)
        used.add(claim_id)
    if used != set(table):
        raise ValueError('unreferenced shared claim metadata')


def pooled_claim_count(request):
    return sum(CLAIM_MARKER in claim for _, _, claim in _claim_entries(request))
