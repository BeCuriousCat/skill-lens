"""Resolve reproducible commands for registered Providers."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def command(spec: dict, source_dir: Path, revision: str, case_id: str, output: Path) -> list[str]:
    if spec["id"] in {"tree-sitter-syntax", "powershell-syntax"} and shutil.which("uv"):
        return [
            "uv", "run", "--python", spec.get("pythonVersion", "3.11"),
            "--with", f"tree-sitter-language-pack=={spec['dependencyVersion']}",
            "python", str(spec["script"]), "--source-dir", str(source_dir),
            "--revision", revision, "--case-id", case_id, "--out", str(output),
        ]
    runtime = sys.executable if spec["runtime"] == "python" else spec["runtime"]
    return [runtime, str(spec["script"]), "--source-dir", str(source_dir),
            "--revision", revision, "--case-id", case_id, "--out", str(output)]


def environment_command(spec: dict) -> list[str]:
    """Return the interpreter prefix for in-process provider tooling."""
    if spec["id"] in {"tree-sitter-syntax", "powershell-syntax"} and shutil.which("uv"):
        return ["uv", "run", "--python", spec.get("pythonVersion", "3.11"), "--with", f"tree-sitter-language-pack=={spec['dependencyVersion']}", "python"]
    return [sys.executable if spec["runtime"] == "python" else spec["runtime"]]
