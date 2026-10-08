---
name: skill-lens
description: Explain a Skill, CLI, Plugin, Hook, or MCP integration from its local source, with evidence-backed capabilities, call paths, limitations, and readable workflow diagrams. Use when inspecting an unfamiliar integration or understanding the impact of a source change.
---

# Skill Lens

Resolve the directory containing this file as the Skill Lens installation root.
Run its tools by absolute path; keep inspected sources, Bundles, and caches
outside that root. Read [consumption instructions](docs/CONSUME.md) for setup
and examples. This is an alpha static-analysis workflow.

Inspect a local source directory with `tools/skill_lens.py inspect`. Use the
actual Git commit as `--revision` for an unchanged checkout. For a modified or
non-Git tree, identify the snapshot honestly rather than presenting an old Git
commit as the current source. No inspected scripts need to be executed.

Use `diagnose` before consuming a Bundle. A valid Bundle can still be partial:
read the diagnostics and recorded Provider results. `--auto-providers` selects
registered routes; missing dependencies can yield a baseline fallback.

For a question, use `ask --limit` to retrieve bounded evidence, then inspect
the cited source spans before explaining the conclusion. `ask` is lexical
retrieval, not an LLM answer. English source identifiers often retrieve better
than translated terms. Use `trace` or `impact` for a bounded static relationship
slice. See [Bundle semantics](docs/LENS_BUNDLE.md) for external projections.

For the main user workflow, use question-driven explanation after inspection:

```bash
python3 tools/skill_lens.py explain <bundle> \
  --question "它为什么会这样回答或行动？" \
  --output <report-directory>
```

默认以中文优先的双语方式呈现外文 Skill。英文原文、标识符、路径和行号必须
原样保留为 Evidence；在报告中为关键结论和已展开源码旁边补充中文理解。翻译
是阅读辅助，不能替代原文或成为新的证据。CLI 本身不偷偷调用翻译服务：当前
Agent 应根据已选源码片段生成一个 graph-bound `skill-lens.translation.v0.1`
sidecar，再用 `explain --translation-file <path>` 渲染。没有译文时仍显示双语
版式，并明确标注“暂无中文对照”，不能把英文复制后标成中文。
若要让普通读者先理解整体机制，sidecar 还可以提供 `overview.summary`、
`overview.mechanismChain` 和 `overview.modules`。这些字段只组织阅读顺序，
模块仍必须绑定当前报告中的源码节点；它们不能把静态规则包装成运行效果证明。
报告还会从机制步骤生成通用的 `decisionRules`：把“输入场景、先判断什么、接着做什么、证据状态”放进同一层。Skill 有固定预设时，翻译 sidecar 可以额外提供 `overview.scenarioPresets`；没有固定预设时，报告仍用 `decisionRules` 表达条件分支，不强行套用不存在的参数档位。

The command classifies source evidence, selects rules relevant to the question,
and follows only bounded, source-backed references. It reports whether static
evidence covers the question. Only when the question asks about a particular
run, or static evidence is incomplete, it searches existing local host records
automatically. “历史执行记录” means records already present in configured
Skill Lens/Codex/Claude trace roots; users do not need to create or paste
input/output examples. Each returned slice stays partial, redacted, and
version-labeled. Reading `SKILL.md` is never proof that a Skill was loaded or
enforced. Use `--static-only` to prohibit historical lookup.

For a source directory, the same command can perform inspection and produce the
report in one step. The Bundle is created temporarily and removed after the
report is validated:

```bash
python3 tools/skill_lens.py explain \
  --source-dir /path/to/target-skill \
  --question "它为什么会这样回答或行动？" \
  --output /path/to/results/explanation
```

For questions about how a prompt makes a capability work, file inventories
and heading counts are insufficient. Select `markdown-sections` so instruction
paragraphs, audience tables, and frontmatter enter the Graph as exact source
spans. Read the complete relevant instructions. Verify the actual Skill
identity: similarly named Skills can contain entirely different mechanisms.

Explain the input/trigger, intended audience, prescribed actions, language or
format constraints, and the reason those constraints could produce the desired
effect. Keep source requirements separate from your interpretation. Do not
invent role-specific behavior when the source only says "explain for a novice."
Show the actual prompt excerpts and a small illustrative comparison when it
helps; label examples as illustrations unless you have real execution evidence.
Describe what remains untested, especially model compliance and reader
understanding. This explains instruction design, not private model reasoning.

Present the few capabilities relevant to the user's question, their source
locations, and remaining unknowns. A small Mermaid workflow can make an
explicitly documented sequence easier to understand. Label document-defined
steps and conditional branches; do not turn lexical order, a call candidate,
or file presence into proof of actual execution. Show disconnected evidence
as disconnected when no relationship is supported.

Distinguish source implementation, documented instructions, and runtime
observations. Static analysis cannot reveal a remote model's private reasoning
or prove host orchestration, model compliance, external effects, or successful
execution. A passing Evidence Gate validates references, not semantic truth.

For prose directly from an existing Projection, use
`tools/render_bundle_explanation.py --output ...`, optionally with
`--source-root` for local links. It formats existing objects and citations;
it does not generate a new workflow. Model projections currently vary between
runs. Do not promise stable object identities or treat an object-count change
as a quality score.

When the user needs to read a Bundle visually, use
`tools/skill_lens.py visualize <bundle> --output <report.html>` and show the
saved report. It includes type/search filters, citation diagrams, original
graph relation slices, and expandable source quotes. Follow
[visual report semantics](docs/VISUAL_REPORT.md): arrows represent citations
or the explicitly named original graph relation, never an invented workflow.
The report is self-contained; the external diagram-design skill is not needed
on the consuming machine.

For a mechanism report, author an optional `instruction-analysis.json` beside
the six required Bundle files, following [the mechanism report contract](docs/VISUAL_REPORT.md#instruction-mechanism-analysis).
Use the existing Graph's `caseId`, `revision`, byte-level SHA-256, and Markdown
span IDs. The CLI validates this sidecar and includes its overview, exact
prompts, role table, illustrative outputs, and unknowns ahead of the evidence
browser. `ask` also exposes it for mechanism questions. This is an analyst
projection: inspection and rendering do not automatically generate it. When a
Graph changes, review and refresh the analysis; never just rebind stale claims.

When the semantic pass will be performed by a weaker model, use
`tools/skill_lens.py mechanism <bundle> --output <prompt.txt>` first. This
creates a bounded, source-only handoff with the Graph hash, exact Markdown
spans, a staged JSON task, and explicit anti-hallucination boundaries. Give
that prompt to the model, save its JSON output, and publish it only through
`tools/skill_lens.py apply-mechanism <bundle> <analysis.json>`. A valid handoff
proves citation integrity and schema conformance; it does not prove semantic
correctness, runtime compliance, or reader comprehension. Read
[the weaker-model handoff guide](docs/MECHANISM_HANDOFF.md) when tuning budgets
or reviewing coverage. Before publishing a candidate, run
`tools/skill_lens.py review-mechanism <bundle> <analysis.json>` to expose cited
span coverage and the remaining semantic/runtime limits; treat that report as
a review aid, never as a semantic score.

Name nodes after their actual content: mechanism steps, intended audiences,
individual rules, functions or operations where applicable. Keep the file as
provenance or a container. A shared source file is not a behavioral relationship.
For an audience Skill, optionally subdivide `audiences.rules` into focus,
language, analogy, tone, depth and structure. Give each rule an exact
`sourceQuote` present in its cited span and verify its applicability from the
surrounding instructions. Do not conflate intended readers with executing
Agents. The report's meaning browser shows these units and their explicitly
authored role-to-rule relationships; the source browser preserves original
Graph edges. Omit semantic links when the analysis has not established them.

The optional Claude projector requires separately configured authentication
and a user-authorized model call. Reading a Bundle and deterministic inspection
do not need a model service.
