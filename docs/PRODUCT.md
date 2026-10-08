# Skill Lens Product Contract

## Purpose

Skill Lens lets a user understand a Skill, CLI, Plugin, Hook, or MCP integration without reading every source file. An explanation must show capabilities, implementation ownership, call flow, unknowns, and the evidence behind each claim.

## Current scope

The first release studies Skills, CLIs, Plugins/Hooks, and MCP registration-to-handler relationships. Initial source formats are JavaScript/TypeScript, Python, Shell, Markdown, JSON, YAML, and TOML.

## Non-goals

- Reconstructing private model reasoning
- Automatically merging code changes
- Supporting every programming language deeply
- Building a multi-tenant cloud service
- Shipping a formal UI before the domain protocol is stable

## Evidence rules

Facts come from providers and traces. Semantic projections may only reference fact node IDs. Missing evidence is represented as `unknown`, `unverified`, or a coverage gap; it must not be filled with a plausible story.

## Execution plan

The product follows one question-driven pipeline:

1. **Freeze the source.** Inspect the Skill into a revision-bound Bundle. The
   Bundle is the immutable evidence boundary; inspection never executes the
   target Skill.
2. **Parse the document and source.** For Markdown, retain exact spans and
   heading scope, then classify candidate roles such as trigger, audience,
   instruction, constraint, output, condition, prohibition, and reference.
   Classifications are hypotheses with lexical signals, not execution facts.
3. **Build the question slice.** Map the natural-language question to facets,
   select relevant rules, and recursively follow only explicit references or
   existing source graph links. Depth, node, character, and missing-reference
   stops remain visible.
4. **Gate static sufficiency.** “Static-sufficient” means the needed source
   facets and actionable rules were located. It does not mean the model obeyed
   them, that a host loaded the Skill, or that a reader understood the answer.
5. **Use history only as a fallback.** If the question names a specific run or
   static evidence is incomplete, the system searches existing local records
   in configured project, Codex, Claude, and Skill Lens trace roots. It never
   asks the user to manufacture an input/output transcript and never replays a
   request. JSON/JSONL records are normalized through host adapters, redacted,
   bounded by file and total-byte budgets, and paired only by explicit call IDs.
6. **Explain in layers.** The report starts with a one-page conclusion, then
   expands exact source quotes, candidate rule interpretation, historical
   observations, recursive stops, and unknowns. Source claims, analyst
   interpretation, and runtime observations remain separate.
7. **Localize without weakening evidence.** For foreign-language sources, the
   default report is Chinese-first and bilingual. A model-generated translation
   sidecar may add Chinese reading text next to exact English quotes; it is
   graph-bound, labeled as machine or human translation, and never treated as
   source Evidence. Missing translations stay visibly unavailable.
8. **Run the understanding loop.** For each Skill type, render a local HTML
   report, give only that HTML to a separate internal reviewer with network
   disabled, collect a 0-100 score across evidence, mechanism, plain language,
   navigation, and boundaries, then turn repeated issues into the next report
   revision. Scores are reader-comprehension observations, not semantic truth.
9. **Review and distribute.** Validate the Evidence Gate, run focused and full
   checks, inspect the offline HTML report, then build a runtime-only
   distribution. A passing gate proves citation integrity, not semantic truth.

The CLI is the internal engineering interface that runs this pipeline. The
product output is the readable Markdown/HTML explanation and its evidence
contract, consumed through the Skill's natural-language entrypoint.

The explanation output follows the versioned
[`skill-lens.explanation-chain.v0.1`](../schemas/skill-lens.explanation-chain.v0.1.schema.json)
contract. Structural validation runs before citation validation, so a report
with missing layers, unsupported statuses, or malformed runtime comparisons
fails closed.

The replay corpus currently has an explanation coverage baseline in
`research/explanation-acceptance-v0.1.json`. It covers source questions
across Markdown replay Bundles. The report records `static-sufficient` or
`static-partial` source coverage only; it deliberately does not turn those
labels into a semantic accuracy percentage.

The first report-understanding loop is recorded in
`research/report-understanding-round1-reviews.json`. It contains one completed
blind read for the design-oriented instruction Skill (80/100) and one pending
case. Cross-type assignments for instruction-only, image workflow, Python
script, plugin hook, and TypeScript MCP cases are listed in
`evals/report-understanding-cross-type-round1.json`; pending cases are never
counted as scores.

## Plan reference

The execution sequence is defined in `skill-lens-master-plan-v1.md` supplied with the project brief. The current repository has progressed through the M6 CLI/replay path and M7 blind-test review preparation. M4, M5, M6, and M7 gates remain explicit and open where comparative semantic or independent reviewer evidence is not yet available; formal UI remains deferred until the M7 gate.
