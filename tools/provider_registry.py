"""Executable Provider registry used by the offline CLI.

Language-specific Providers register capabilities here; orchestration remains
language-agnostic and keeps the same timeout, fallback, and Bundle path.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent

PROVIDERS = {
    "stdlib-baseline": {
        "id": "stdlib-baseline",
        "version": "0.1.3",
        "runtime": "python",
        "script": ROOT / "provider_baseline.py",
        "languages": ["markdown", "json", "yaml", "toml", "shell", "unknown"],
        "facets": ["inventory", "locations", "frontmatter", "script-facts", "hook-static-declarations", "quoted-hook-json-literals"],
        "unsupportedFacets": ["symbol-resolution", "runtime-dispatch", "framework-semantics", "hook-runtime", "general-shell-control-flow"],
        "dependencies": [],
        "dependencyPolicy": "stdlib-only",
        "cacheBehavior": "orchestrator-content-addressed",
        "failureModes": ["unreadable-file", "unsupported-language", "partial-scan"],
        "routingExtensions": {},
    },
    "python-ast": {
        "id": "python-ast",
        "version": "0.4.1",
        "runtime": "python",
        "script": ROOT / "provider_python_ast.py",
        "languages": ["python"],
        "facets": ["function-definitions", "imports", "syntactic-calls", "conservative-local-call-resolution", "conservative-cross-file-call-resolution", "lexical-statement-context"],
        "unsupportedFacets": ["runtime-dispatch", "framework-semantics", "dynamic-imports", "alias-resolution", "re-export-resolution"],
        "dependencies": [],
        "dependencyPolicy": "stdlib-only",
        "cacheBehavior": "orchestrator-content-addressed",
        "failureModes": ["parse-failure", "unsupported-syntax", "unresolved-call"],
        "routingExtensions": {".py": "python"},
        "contractFixture": {
            "files": {"main.py": "def greet():\\n    return 'ok'\\n\\ngreet()\\n"},
            "requiredNodeLabels": ["main.py:greet"],
            "requiredEdgeTypes": ["declares"],
            "requiredEvidenceQuotes": ["function definition greet"],
        },
    },
    "shell-syntax": {
        "id": "shell-syntax",
        "version": "0.1.0",
        "runtime": "python",
        "script": ROOT / "provider_shell.py",
        "experimental": True,
        "languages": ["shell"],
        "facets": ["function-definitions", "command-candidates", "loops"],
        "unsupportedFacets": ["command-resolution", "quoting-and-substitution", "remote-effects"],
        "dependencies": [],
        "dependencyPolicy": "stdlib-only",
        "cacheBehavior": "orchestrator-content-addressed",
        "failureModes": ["parse-failure", "unsupported-shell-construct", "unresolved-command"],
        "routingExtensions": {".sh": "shell", ".bash": "shell", ".zsh": "shell"},
        "contractFixture": {
            "files": {"worker.sh": "run() {\\n  curl https://example.test/job\\n}\\n"},
            "requiredNodeLabels": ["worker.sh:run"],
            "requiredEdgeTypes": ["declares"],
            "requiredEvidenceQuotes": ["shell function definition run"],
        },
    },
    "powershell-syntax": {
        "id": "powershell-syntax",
        "version": "0.1.0",
        "runtime": "python",
        "script": ROOT / "provider_powershell.py",
        "experimental": True,
        "languages": ["powershell"],
        "facets": ["function-definitions", "conditionals", "loops", "command-candidates", "syntax"],
        "unsupportedFacets": ["command-resolution", "module-resolution", "runtime-dispatch", "framework-semantics", "complete-grammar"],
        "dependencies": ["tree-sitter-language-pack==1.20.0"],
        "dependencyPolicy": "pinned-optional",
        "cacheBehavior": "orchestrator-content-addressed",
        "failureModes": ["dependency-unavailable", "parse-failure", "syntax-errors", "unsupported-language", "timeout"],
        "pythonVersion": "3.11",
        "dependencyVersion": "1.20.0",
        "routingExtensions": {".ps1": "powershell"},
        "contractFixture": {
            "files": {"hook.ps1": "function Invoke-Demo {\n    if ($true) {\n        Write-Output \\\"ok\\\"\n    }\n}\n"},
            "requiredNodeLabels": ["hook.ps1:Invoke-Demo", "hook.ps1:Write-Output"],
            "requiredEdgeTypes": ["declares", "contains", "invokes"],
            "requiredEvidenceQuotes": ["PowerShell function-definitions Invoke-Demo", "PowerShell command-candidates Write-Output"],
        },
    },
    "tree-sitter-syntax": {
        "id": "tree-sitter-syntax",
        "version": "0.3.0",
        "runtime": "python",
        "script": ROOT / "provider_tree_sitter.py",
        "languages": ["python", "typescript", "javascript", "shell", "markdown", "json", "yaml", "toml"],
        "facets": ["syntax", "locations", "definitions", "imports", "callbacks", "config-scalar-fields"],
        "unsupportedFacets": ["semantic-symbol-resolution", "runtime-dispatch", "framework-semantics"],
        "dependencies": ["tree-sitter-language-pack==1.20.0"],
        "dependencyPolicy": "pinned-optional",
        "cacheBehavior": "orchestrator-content-addressed",
        "failureModes": ["dependency-unavailable", "parse-failure", "unsupported-language"],
        "pythonVersion": "3.11",
        "dependencyVersion": "1.20.0",
        "routingExtensions": {
            ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
            ".tsx": "typescript", ".sh": "shell", ".bash": "shell", ".json": "json",
            ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".markdown": "markdown",
            ".toml": "toml",
        },
    },
    "typescript-compiler": {
        "id": "typescript-compiler",
        "version": "0.3.0+ts5.6.2",
        "runtime": "node",
        "script": ROOT / "provider_typescript_compiler.mjs",
        "languages": ["typescript", "javascript"],
        "facets": ["local-symbol-resolution", "resolved-call-candidates", "lexical-statement-context"],
        "unsupportedFacets": ["external-package-semantics", "runtime-dispatch", "framework-semantics", "incremental-analysis"],
        "dependencies": ["typescript@5.6.2"],
        "dependencyPolicy": "runtime-bundled",
        "cacheBehavior": "orchestrator-content-addressed",
        "failureModes": ["runtime-unavailable", "parse-failure", "unresolved-symbol", "timeout"],
        "routingExtensions": {
            ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
            ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        },
        "contractFixture": {
            "files": {"target.ts": "export function act() { return 1; }\\n", "entry.ts": "import { act } from './target.js';\\nact();\\n"},
            "requiredNodeLabels": ["target.ts:act"],
            "requiredEdgeTypes": ["resolves-symbol"],
            "requiredEvidenceQuotes": ["definition act:"],
        },
    },
    "markdown-sections": {
        "id": "markdown-sections",
        "version": "0.2.0",
        "runtime": "node",
        "script": ROOT / "provider_markdown_sections.mjs",
        "languages": ["markdown"],
        "facets": ["source-spans", "headings", "lists", "code-blocks", "tables", "frontmatter"],
        "unsupportedFacets": ["instruction-semantics", "runtime-behavior", "ownership"],
        "dependencies": ["markdown-it@15.0.2"],
        "dependencyPolicy": "runtime-bundled",
        "cacheBehavior": "orchestrator-content-addressed",
        "failureModes": ["runtime-unavailable", "parse-failure", "unsupported-markdown-extension"],
        "routingExtensions": {".md": "markdown", ".markdown": "markdown"},
        "contractFixture": {
            "files": {"SKILL.md": "# Demo\\n\\n## Modes\\n\\n- Built-in preferred.\\n"},
            "requiredNodeLabels": ["SKILL.md:1", "SKILL.md:3"],
            "requiredEdgeTypes": ["contains"],
            "requiredEvidenceQuotes": ["# Demo", "## Modes"],
        },
    },
}

def _extension_languages():
    routes = {}
    for name, spec in PROVIDERS.items():
        for extension, language in spec.get("routingExtensions", {}).items():
            if language not in spec["languages"]:
                raise ValueError(f"extension {extension} maps to undeclared language {language}")
            if extension in routes and routes[extension] != language:
                raise ValueError(f"extension {extension} has conflicting language routes")
            routes[extension] = language
    return routes


EXTENSION_LANGUAGES = _extension_languages()
REGISTERED_ROUTING_EXTENSIONS = frozenset(EXTENSION_LANGUAGES)

UNSUPPORTED_SOURCE_EXTENSIONS = {
    ".rs", ".go", ".java", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".swift", ".kt", ".kts", ".dart", ".lua", ".r", ".scala", ".ex", ".exs",
    ".fs", ".fsx", ".cs", ".sol",
}


def names():
    return tuple(sorted(PROVIDERS))


def get(name):
    try:
        return PROVIDERS[name]
    except KeyError as error:
        raise ValueError(f"unsupported provider: {name}; available: {', '.join(names())}") from error


def detect(source_dir):
    """Return precise routes supported by file extensions in source_dir.

    The baseline is intentionally omitted: the CLI always adds it separately.
    Provider order is stable and follows registry name order.
    """
    languages = set()
    for path in Path(source_dir).rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        language = EXTENSION_LANGUAGES.get(path.suffix.lower())
        if language:
            languages.add(language)
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                first_line = handle.readline().strip().lower()
        except OSError:
            continue
        if first_line.startswith("#!"):
            if "python" in first_line:
                languages.add("python")
            elif any(token in first_line for token in ("node", "deno", "bun")):
                languages.add("javascript")
            elif any(token in first_line for token in ("bash", "sh", "zsh")):
                languages.add("shell")
    routes = plan(languages)
    # Markdown is the primary Skill instruction format. Every automatic
    # inspection includes its exact source spans so question explanations do
    # not silently degrade to file inventories and headings.
    has_markdown = any(path.is_file() and path.suffix.lower() in {'.md', '.markdown'}
                       and '.git' not in path.parts for path in Path(source_dir).rglob('*'))
    if has_markdown and 'markdown-sections' not in routes:
        routes.append('markdown-sections')
    return sorted(routes)


def plan(languages, requested_facets=None):
    """Return stable routes covering requested facets for detected languages."""
    languages = set(languages)
    facets = set(requested_facets or ())
    eligible = []
    for name in names():
        spec = PROVIDERS[name]
        if name == "stdlib-baseline" or spec.get("experimental"):
            continue
        if not languages.intersection(spec["languages"]):
            continue
        eligible.append(name)
    if not facets:
        return eligible
    return [name for name in eligible if facets.intersection(PROVIDERS[name]["facets"])]


def facet_coverage(provider_names, requested_facets):
    """Describe which selected Providers claim each requested facet."""
    result = {}
    for facet in sorted(set(requested_facets)):
        providers = sorted(name for name in set(provider_names) if facet in PROVIDERS[name]["facets"])
        result[facet] = {"providers": providers, "status": "available" if providers else "unavailable"}
    return result


def unsupported_extensions(source_dir):
    """Return stable source-language extensions with no registered route."""
    return sorted({
        path.suffix.lower()
        for path in Path(source_dir).rglob("*")
        if (
            path.is_file()
            and ".git" not in path.parts
            and path.suffix.lower() in UNSUPPORTED_SOURCE_EXTENSIONS
            and path.suffix.lower() not in REGISTERED_ROUTING_EXTENSIONS
        )
    })


def manifest():
    """Return stable, JSON-ready capability metadata for diagnostics and routing."""
    return {
        "schema": "skill-lens.provider-registry.v0.1",
        "providers": [
            {
                "id": spec["id"],
                "version": spec["version"],
                "runtime": spec["runtime"],
                "languages": list(spec["languages"]),
                "facets": list(spec["facets"]),
                "unsupportedFacets": list(spec["unsupportedFacets"]),
                "dependencies": list(spec["dependencies"]),
                "dependencyPolicy": spec["dependencyPolicy"],
                "cacheBehavior": spec["cacheBehavior"],
                "failureModes": list(spec["failureModes"]),
                "routingExtensions": dict(spec.get("routingExtensions", {})),
                "experimental": bool(spec.get("experimental")),
                "entrypoint": str(spec["script"]),
            }
            for name, spec in sorted(PROVIDERS.items())
        ],
    }
