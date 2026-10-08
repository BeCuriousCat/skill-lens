# 让较弱模型也能做机制分析

Skill Lens 可以把“为什么这个 Skill 能产生这种表达”拆成一项受约束的子任务，交给能力较弱的模型完成。它不是让模型自由总结，而是给模型一份只包含原文片段的证据包，并要求输出固定的 `instruction-analysis.v0.1` JSON。

```bash
python3 tools/skill_lens.py mechanism \
  /path/to/bundle \
  --question "它如何按受众调整表达？" \
  --output /path/to/mechanism-prompt.txt
```

生成的 Prompt 包含 Bundle 身份、确切文件和行号的 Markdown span、源码数据边界、七步分析任务和 JSON 合同。模型输出后必须经过：

```bash
python3 tools/skill_lens.py apply-mechanism \
  /path/to/bundle /path/to/model-output.json
```

`apply-mechanism` 会检查 Graph 身份、Evidence 节点、原文行号和 `sourceQuote`。失败不会替换已有 sidecar。通过只证明引用和结构有效，不证明语义正确、Agent 遵循或读者理解。

发布前还可以运行确定性的覆盖审查：

```bash
python3 tools/skill_lens.py review-mechanism \
  /path/to/bundle /path/to/model-analysis.json \
  --output /path/to/mechanism-review.json
```

它报告被引用的 Markdown span 数量、来源文件、阶段和受众数量，并固定列出三类不能由 Evidence Gate 证明的事项：语义解释是否正确、模型是否遵循、读者是否真的理解。这个审查不会给语义质量打分，也不会把覆盖率当成质量分数。

## 为什么弱模型也能跟上

Provider 先做确定性的定位，Prompt 再给出有限分类，Evidence Gate 拒绝无出处结论，报告最后才做可视化。弱模型不用同时完成搜索、源码定位、分类、写作和引用管理。

要把一组不同 Skill 一起交给模型，可以先批量生成受限 Prompt：

```bash
python3 tools/build_mechanism_case_matrix.py \
  /path/to/bundles \
  --output-dir /tmp/skill-lens-mechanism-prompts
```

输出目录中的 `matrix.json` 会记录每个案例的证据总量、实际交给模型的片段数、遗漏片段数和来源文件。这样可以先比较覆盖边界，再决定哪些案例需要扩大预算或分批分析。

`instruction` 是源码要求的转述，`interpretation` 是可能作用的解读；二者不能混写。每个角色最多四条规则，每条使用原文中的精确 `sourceQuote`。没有明确角色规则就留空，不能因为共享文件或表格而创造关系。

这套流程可复用到 ELI5、输出风格、图片生成、Skill Creator、MCP 等样例。大文件的 Prompt 有字符和条数上限；覆盖不足时应扩大预算，而不是让模型猜缺失内容。它不能还原远程模型私有推理，也不能证明实际执行效果。
