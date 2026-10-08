# Lens Bundle v0.1

A Lens Bundle is an offline-readable result for one Artifact revision:

For readable prose with source references, run
`python3 tools/render_bundle_explanation.py <bundle> --output <report.md>`.
Use `--source-root <retained-source-directory>` to link citations to local
source files. The renderer groups the existing Projection objects and expands
their Evidence references; it does not generate an answer or infer execution
order. Invalid Bundles are rejected. A valid citation still requires a semantic
check against its source.

```text
artifact.json
evidence-graph.json
semantic-projection.json
diagnostics.json
sources.lock.json
analysis-manifest.json
```

`artifact.json` identifies the source and revision. `evidence-graph.json` is the merged canonical graph. `semantic-projection.json` is the evidence-linked explanation. `diagnostics.json` contains coverage gaps, conflicts, provider failures, and validation results. `sources.lock.json` records source and license provenance. `analysis-manifest.json` records provider versions, schema versions, run IDs, and timestamps.

Bundles must be safe to read offline, must not contain secrets, and must distinguish raw provider data from derived projections. A missing provider produces diagnostics and a partial bundle instead of invalidating already completed evidence.

`tools/build_lens_bundle.py` assembles the six files from verified inputs and rejects a projection whose case, revision, or Evidence node references do not match the graph. `tools/skill_lens.py inspect` runs the dependency-free baseline plus each explicitly selected or extension-detected language Provider and writes a Bundle with a visible partial status when diagnostics exist. If a requested Provider fails, the CLI preserves successful patches and records the failure in diagnostics and the manifest. Unsupported source-language extensions also become explicit coverage gaps. The CLI still uses rule-based projection rather than the semantic model projector.

The six fixtures in `evals/lens-bundles/golden-candidates/` demonstrate an offline candidate Projection over each golden Evidence Graph. They are evaluation fixtures; a production Bundle must use the GraphPatch generated from the inspected revision and record the actual Provider run.

`tools/validate_lens_bundle.py` validates all required files, cross-file case and revision identity, semantic references, and suspicious credential fields before a Bundle is shared. `diagnose` applies the same Bundle-level checks. A failed language Provider or an unsupported source-language extension is represented by a fallback or coverage-gap diagnostic, a `has-coverage-gap` edge, and an Uncertainty object backed by the orchestrator's run observation. This records missing analysis coverage, not a claim about source behavior.

An external Semantic Projection can be applied with `tools/apply_projection.py`. It must pass the projection Evidence Gate and full Bundle validation first; the tool stages all six documents and atomically publishes the replacement Bundle, recording `projection: external-file-v0.1` plus the input filename in the analysis manifest. Invalid projections or publication failures leave the existing Bundle unchanged.

`tools/run_projection_command.py` provides a model-agnostic projector handoff:
it sends a `skill-lens.semantic-projector-request.v0.1` JSON document containing
the graph and questions to an executable's stdin, accepts only JSON stdout, and
rejects the result unless the full Evidence Gate passes. It records runtime,
stdout hash, and evaluator results in a separate run record. The command is
passed as an executable plus explicit `--arg` values; no shell string is
evaluated. An optional `--question-slices` input adds bounded, source-linked
question slices to the request and records the slice count in the run metadata.
Only question IDs and question text are sent; benchmark answer requirements,
forbidden claims, expected Evidence, and unknown annotations remain local to
the evaluator. When slices are provided, only the selected subgraph and its
selected Evidence are sent. The initial Evidence Gate rejects references to
unselected nodes, even when they exist in the original graph. Run metadata
records request hash, character count, and input isolation policy.
The original Markdown-oriented slice payload follows
`schemas/skill-lens.question-slices.v0.1.schema.json`. A generic Evidence Graph
slice may instead use `skill-lens.evidence-question-slices.v0.1`; it preserves
selected nodes, source Evidence, and edges between selected nodes. Both forms
are validated against the same GraphPatch before handoff.
Generic retrieval splits qualified/camelCase identifiers and uses limited
source-operation query aliases. For compiler call nodes with a source-statement
parent, it keeps that statement's lexical context in the same bounded selection.
If the call and required context cannot fit, the call is excluded rather than
silently stripping its guards. These retrieval rules do not prove execution or
semantic answerability.
Before handoff, every selected `nodeId` and `evidenceId` must exist in the
same GraphPatch, the Evidence must be attached to that node, and its source
revision must equal the graph revision. This keeps a language Provider's
query-scoped evidence bounded to the graph being projected; a slice is still
lexical retrieval context and does not prove semantic answerability.
Slice budgets apply per question to retrieval selections; the runner records
the actual serialized request size separately and does not claim an exact
token budget. Historical runs made before this input isolation policy are
documented in `research/semantic-projection/input-isolation-audit-v0.1.md`.
Use `apply-projection` afterward to atomically publish a validated Projection
into a Bundle.

Bundle writers stage all six files in a private directory and validate the staged result before replacing the destination. A failed write leaves the previously published Bundle intact; `tools/test_bundle_writer.py` covers this rollback invariant.

`skill_lens.py inspect` applies the timeout to the TypeScript Compiler subprocess. A timeout produces a warning diagnostic and can fall back to the baseline Provider; the timeout value and fallback reason remain in `analysis-manifest.json`.

For repeat analyses, pass `--cache-dir <directory>`. The CLI stores each
validated GraphPatch under a key derived from case ID, revision, Provider ID and
version, the resolved source root, source paths/contents, adapter/support code,
runtime executable/version, and external parser/compiler inputs. File hashing
excludes only `.git`, matching the current Providers' inventories; it includes
`node_modules` and `__pycache__` because the baseline may read their files.
The TypeScript Provider and cache probe share the same program construction;
before lookup the probe resolves source/declaration files, compiler code, and
ancestor package metadata, including relative imports outside the artifact.
This parsing/fingerprinting has a cost and is not an incremental-analysis claim.

A cache hit is recorded in `analysis-manifest.json`, together with the source
and execution fingerprints. Source edits, code changes at the same declared
version, dependency changes, or a changed revision/version produce a new key.
Cache entries are validated before reuse and written atomically. Invalid entries
are repaired after recomputation. Fingerprinting or cache write failures record
`bypass` and preserve successful Provider results; they are not recorded as
analysis failures. Inputs changed during a run are not cached. Keep the cache
outside the source tree so cached outputs cannot become input facts; the CLI
rejects a nested cache path before publishing anything.

`evals/lens-bundles/provider-smoke/` contains six Bundles generated from the locked temporary source checkouts through `skill_lens.py inspect`. These are the first real-source end-to-end smoke results; their `partial` status is expected because the baseline Provider explicitly reports semantic limitations.

`skill_lens.py ask` first searches Projection labels and summaries. If no
Projection object matches, it can return `source-evidence matches`: bounded
lexical matches over node labels and attached source Evidence. That response
is marked `semanticAnswer: not generated` and must be treated as retrieval
context rather than a semantic conclusion.

`skill_lens.py summary <bundle>` renders the same Bundle as a compact terminal
report. It shows revision, status, Projection provenance, recorded Providers,
graph counts, Projection objects, and coverage boundaries; `--format json`
returns the summary structure for scripts without exposing Provider-specific
files. A `rule-based-v0.1` Projection is an evidence-linked retrieval aid, not
a model-generated semantic answer.

For a multi-case overview, `tools/report_bundle_summary.py` writes a Markdown
table and Projection highlights. The current replay index is archived at
`research/replay-bundle-summary-v0.1.md`.
