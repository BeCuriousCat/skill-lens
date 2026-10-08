"""Content-addressed cache for validated Provider GraphPatches."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_graph_patches import validate


EXCLUDED_DIRS = {".git"}


def source_fingerprint(source_dir: Path) -> dict:
    """Hash paths and bytes so a reused revision cannot hide local edits."""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise ValueError(f"source directory does not exist: {source_dir}")
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted(source_dir.rglob("*")):
        relative = path.relative_to(source_dir)
        if not path.is_file() or any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        name = relative.as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big")); digest.update(name)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
        file_count += 1
    return {"algorithm": "sha256-path-and-content-v0.1", "digest": digest.hexdigest(), "fileCount": file_count}


def files_digest(paths):
    digest = hashlib.sha256()
    for path in sorted({Path(item).resolve() for item in paths}):
        label = str(path).encode("utf-8")
        digest.update(len(label).to_bytes(8, "big") + label)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def execution_identity(spec, source_dir):
    script = Path(spec["script"]).resolve()
    runtime = sys.executable if spec["runtime"] == "python" else shutil.which(spec["runtime"])
    if not runtime:
        raise ValueError("Provider runtime is unavailable for cache fingerprinting")
    version = subprocess.run([runtime, "--version"], check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    code = [*script.parent.glob("*.py"), *script.parent.glob("*.mjs"), script]
    dependencies = []
    if spec["id"] == "typescript-compiler":
        probe = subprocess.run([runtime, str(script.parent / "typescript_cache_inputs.mjs"), str(source_dir)],
                               check=True, capture_output=True, text=True, timeout=60)
        dependencies = json.loads(probe.stdout)
    elif spec["id"] == "tree-sitter-syntax":
        # Hash installed parser packages, including their native libraries.
        probe_code = "import importlib.metadata as m,json; print(json.dumps([str(d.locate_file(f)) for n in ('tree-sitter', 'tree-sitter-language-pack') for d in [m.distribution(n)] for f in (d.files or []) if str(f).endswith(('.py','.so','.pyd','.dll','.dylib'))]))"
        probe = subprocess.run([runtime, "-c", probe_code], check=True, capture_output=True, text=True, timeout=10)
        dependencies = json.loads(probe.stdout)
        if not dependencies:
            raise ValueError("Parser package inputs unavailable for cache fingerprinting")
    elif spec["id"] not in {"stdlib-baseline", "python-ast", "shell-syntax", "markdown-sections"}:
        raise ValueError("Provider cache dependency scope is not declared")
    return {"adapterSha256": hashlib.sha256(script.read_bytes()).hexdigest(),
            "supportCodeSha256": files_digest(code), "runtimePath": str(Path(runtime).resolve()),
            "runtimeVersion": version, "runtimeSha256": files_digest([runtime]),
            "dependencyFileCount": len(dependencies), "dependencySha256": files_digest(dependencies)}


def cache_key(source_dir: Path, case_id: str, revision: str, provider: dict, execution=None) -> tuple[str, dict]:
    fingerprint = source_fingerprint(source_dir)
    material = {"schema": "skill-lens.provider-cache-key.v0.2", "caseId": case_id, "revision": revision,
                "provider": provider, "sourceRoot": str(Path(source_dir).resolve()), "source": fingerprint,
                "execution": execution}
    key = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return key, fingerprint


def load_cached(cache_dir: Path, key: str, provider: dict, case_id: str, revision: str) -> dict | None:
    path = Path(cache_dir) / f"{key}.graph-patch.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        validate(path)
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if graph.get("provider") != provider or graph.get("caseId") != case_id or graph.get("revision") != revision:
        return None
    return graph


def store_cached(cache_dir: Path, key: str, graph: dict) -> None:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{key}.graph-patch.json"
    if target.is_symlink():
        raise ValueError("cache destination must not be a symlink")
    handle, temporary = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=cache_dir)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(graph, stream, ensure_ascii=False, indent=2); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
