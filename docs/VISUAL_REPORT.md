# 离线可视化报告

```bash
python3 tools/skill_lens.py visualize /path/to/bundle --output /path/to/report.html
```

也可直接运行 `tools/render_bundle_visualization.py`，参数相同。
输出必须是 Bundle 目录之外的 `.html` 文件。双击打开即可使用，不需要服务器、
网络、模型服务或额外安装 diagram-design。模板、样式和交互已进入 alpha 分发。

报告提供解释类型筛选、源码关键词搜索、分页、明暗主题、证据原文展开、
引用关系图和源码关系切片。规则 Bundle 与外部模型 Projection 均可读取；
模型输出的冗长或缺漏不会被渲染器偷偷改写。

“证据引用图”根据每个 Projection 对象已经验证的 `evidenceNodeIds`，
展开其 Evidence 引用。只有一段原文时直接展示证据；文件存在这类条目也
不单独画引用图。原文节点用章节或指令开头区分，文件名与行号作为出处。
图不会把同一文件的不同片段显示成多个没有区别的文件节点。

“源码关系”只画原始图里的边，并保留关系类型、
端点身份、属性和原文证据。每组最多 4 条关系，逐条排列，不表示它们依次执行。
没有记录的关系不会补画，文档引用不会被变成脚本调用。
单独标注的“指令作用拆解”是分析者解读，语义见下文，不是源码关系图。
如果关联边都是文档的 `contains` / `declares`，视图称为“文档结构”，并说明
它只表达归属，不表达规则协作或 Agent 执行。相同父文档的包含边合并展示
一个父节点与多个片段；全部原始边和身份仍保留。文件对应的片段优先展示，
其余扫描元数据也可分页查看。带机制分析的报告默认从机制、角色与规则这些
内容单元开始阅读；可切换到源码层次核对原始片段和文件记录。

大 Bundle 不一次性渲染所有条目或画完整大图。所有 Projection、原始 Graph
端点、边和 Evidence 均保留在报告中；每页 12 个解释对象，图上最多 5 个引用
节点或 8 个关系端点。缩略标签有省略号，原始字符串和源码引用不被截断。
未知项可单独筛选，诊断与来源哈希位于报告底部。

所有源码内容按文本处理，内嵌 JSON 转义脚本结束符，报告禁止网络连接。
有效引用只说明证据可以定位，不是语义准确性或真实执行的证明。
报告包含源码引用文本，分享前应确认被分析内容适合分享。

禁用 JavaScript 时保留前 12 条解释的静态文本预览。打印当前选择与当前
关系组；如果需要完整文字报告，使用 `render_bundle_explanation.py`。

布局与视觉层次借鉴 diagram-design；具体取舍和字体替代见 [DESIGN.md](../DESIGN.md)。
这是 CLI 输出能力，正式 M8 应用界面和其他语义验收门禁保持原有范围。

## Instruction mechanism analysis

当读者关心“它怎样实现能力、提示词怎么写、为什么这种约束可能有效”时，
需要分析指令内容。只画文件和 heading 不能回答这些问题。

`markdown-sections` v0.2.0 提取段落、完整 Markdown 表格与独立 frontmatter
的准确原文范围；frontmatter 不再被误识别为 Markdown 标题。它依然只是
事实 Provider，**不会自动生成机制解释**。机制分析由分析者或被授权的模型
根据原文编写，是额外的语义投影；图有效不代表解释已经通过语义评测。

把 `instruction-analysis.json` 放在 Bundle 中，可由 `visualize` 和 `ask`
自动读取。也可以用 `--instruction-analysis /path/to/analysis.json` 显式指定。
原来的六个必需文件与 Semantic Projection 协议保持兼容，没有 sidecar 的
Bundle 照常展示。附带 sidecar 时 `diagnose` 会一并检查。

结构见 [instruction-analysis schema](../schemas/skill-lens.instruction-analysis.v0.1.schema.json)：

- `caseId`、`revision`、`graphSha256`：绑定具体 Graph 文件的身份和字节 SHA-256。
- `title`、`identity`、`summary`、`scopeNote`：读者需要知道的对象与版本差异。
- `stages`：2–7 项指令作用；每项含 `id`、`label`、`instruction`（原文要求的解释）、
  `interpretation`（分析者对作用的解读）、`evidenceNodeIds`。
- `prompts`：`label` 和 `evidenceNodeIds`；实际提示词从 Graph 原文读取，不能替换成改写。
- `audiences`：角色、原文规定的表达规则与引用；没有角色规则时保留空数组。
  可选 `rules` 将一个角色细分为 1–4 个规则，每条含 `id`、`label`、`kind`、
  `instruction`、`evidenceNodeIds`、`sourceQuote`。`kind` 分别表示关注重点、语言、
  类比、语气、细节深度或组织方式。`sourceQuote` 必须是所引 Evidence 中准确
  存在的片段；报告根据其字符位置计算对应行号，可从同一表格精确定位不同角色行。
- `illustrations`：受众、示意输入、示意输出、规则作用和引用；永远按分析者演示展示。
- `unknowns`：待验证的问题与解释；至少包含一项，避免把静态分析当成效果证明。

引用必须指向有准确文件位置与非空 doc 原文的 `markdown-span` 节点。
文件存在、标题计数等占位证据不能支撑机制分析。代码块示例不能充当行为指令。
身份或 Graph 哈希不匹配会拒绝渲染；检查引用仍不能自动判断全部语义正确性。

图中虚线表示**分析者组织解释的顺序**，不是 Graph 边、运行时间线或模型思考轨迹。
原文要求、作用解释、分析者示例与未验证部分有独立标签。核心机制和原始提示词
由 Python 输出为静态 HTML，禁用 JavaScript 也完整可读；JS 仅增加读者示例切换。
打印时展示全部示例。

`ask` 对机制、提示词或受众问题附带已有分析，并将状态标记为 analyst interpretation。
它不会依据问题重新推理，也不是自动问答或执行评测。

## 内容单元与源码单元

一个文件可以承载多个受众、多条规则和多个解释步骤。文件是容器，不应充当
这些不同概念的共同名称。有机制分析时，阅读器默认进入“理解能力”：

| 节点类型 | ELI5 示例 | 连线含义 |
| --- | --- | --- |
| 机制步骤 | 识别读者、校准语言类比 | 解释组织顺序在上方机制概览单独标注 |
| 受众角色 | 经理、工程师、5 岁儿童 | 对该受众适用哪些表达规则 |
| 表达规则 | 关注影响与成本、保留专业术语 | 所属受众与来源证据 |

这些是分析者写入机制投影的内容单元，有各自的展示 ID；没有改写原始 Graph。
“经理 → 围绕决策表达”表示文档中的适用要求，不能解释成多个执行 Agent
之间通信。不能仅因为规则引用同一文件或同一表格就推断它们相互依赖。
每个受众的规则来自显式编写、带原文片段的 `audiences.rules`；缺少细分时不补画。

“核对源码”切换回完整原始 Projection 和 Graph，继续显示真实 `contains`、
`declares`、调用等边。内容单元的标题显示其含义，文件名、行号与片段 ID
作为出处。点击角色表里的读者名称也可直接打开该角色的规则关系图。

禁用 JavaScript 时，角色表依然列出全部细分规则。分类筛选、角色关系图与
逐条定位在启用 JavaScript 后可用。原文匹配能保证引用存在，仍不能自动证明
“这个角色适用这条规则”的全部语义正确性；分析者必须核对原文上下文。
