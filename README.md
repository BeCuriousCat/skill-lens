# 在另一台机器消费 Skill Lens alpha

## 最新更新

2026-10-09：报告现在会把 Skill 的决策逻辑整理成“输入场景 → 判断 → 动作”。有固定预设的 Skill 会继续显示“场景 → 预设 → 参数”；没有固定预设的 Skill 也会显示条件分支、动作和证据状态。报告仍会区分源码声明与实际运行记录。

完整记录见 [发布记录](docs/releases/release-notes.md)。

当前交付支持源码静态透视、问题驱动解释、离线 Bundle 查询和证据报告。正式交互界面尚未完成。
现在也包含可直接打开的离线 HTML 可视化报告，说明见 [VISUAL_REPORT.md](docs/VISUAL_REPORT.md)。
默认 `inspect` 生成规则 Projection；`ask` 返回检索结果，不自动调用模型。

## 解释一个问题

用户不需要准备或粘贴一份输入输出记录。Skill Lens 会先使用 Bundle 中的
静态源码回答问题；只有问题指向某次实际运行，或静态覆盖不足时，才会自动
从已有的本地 Codex/Claude/项目 Trace 根目录寻找相关记录。找到的只是脱敏、
有预算限制的历史片段，并且会标注 `matched`、`unknown` 或 `conflicting` 的
Skill 版本绑定。读取 `SKILL.md` 不等于 Skill 被加载，日志中提到名称也只是
关联线索。

```bash
python3 tools/skill_lens.py explain /path/to/bundle \
  --question "它为什么会这样回答或行动？" \
  --output /path/to/results/explanation
```

也可以直接对源码目录提问。此模式自动计算干净 Git 提交或文件树哈希，
完成 Provider 检查，验证解释报告后删除临时 Bundle：

```bash
python3 tools/skill_lens.py explain \
  --source-dir /path/to/target-skill \
  --question "它为什么会这样回答或行动？" \
  --output /path/to/results/explanation
```

报告先给一页结论，再展开精确源码证据、规则分类、递归追踪的停止原因、
历史观察和未知项。要严格禁止历史回溯时加 `--static-only`。

外文源码默认使用中文优先的双语版式。英文原文仍是精确证据，中文译文标为
“中文理解”，不会改变 Evidence。若当前 Agent 能提供模型译文，可先写一个
绑定同一 `graphSha256` 的 `skill-lens.translation.v0.1` sidecar，再传入：

```bash
python3 tools/skill_lens.py explain /path/to/bundle \
  --question "它为什么会这样回答或行动？" \
  --translation-file /path/to/translation.json \
  --output /path/to/results/explanation
```

sidecar 只允许引用当前报告的 Claim ID 和源码节点 ID。没有 sidecar 时，报告
仍显示中文优先的结构，但会明确写出“暂无中文对照”；这比伪造译文更安全。
可选的 `overview` 用于首屏产品表达：`summary` 是中文一页结论，
`mechanismChain` 是输入到输出的有序步骤，`modules` 为每步提供 Agent 动作、
读者检查项和源码节点。它只改善导航与理解，不增加新的 Evidence。

## 安装

使用 Python 3.11 或更新版本。Python Providers 使用启动 CLI 的解释器，
不要求系统额外提供名为 `python` 的命令。
完整语言路由还需要 Node.js 20 或更新版本，以及 `uv`。
TypeScript/Markdown 依赖通过安装目录里的锁文件安装：

```bash
cd /path/to/skill-lens
npm ci --ignore-scripts
python3 tools/smoke_install.py
python3 tools/smoke_install.py --auto-providers
```

`uv` 可解析 Python 3.11 与锁定的 `tree-sitter-language-pack==1.20.0`，
第一次使用可能需要联网下载。缺少可选依赖时，检查诊断中的 fallback；
不能把成功写出 Bundle 当成所有语言分析均成功。

作为 Codex Skill 使用时，把干净的 `skill-lens` 文件夹放到目标机器的
`~/.codex/skills/skill-lens`（或已配置的技能目录），保持 `SKILL.md`、
`tools/`、`schemas/`、`docs/` 和 package 锁文件在同一个根目录。
然后在新会话中用 `$skill-lens` 请求分析本地项目。
也可以把候选仓库直接 clone 到这个目录；不要只复制 `SKILL.md`。

## CLI 示例

在 Skill Lens 安装目录执行以下命令，将占位路径替换为实际路径。
对未修改的 Git checkout，revision 使用其真实 `git rev-parse HEAD`。
源码有未提交修改时，应先保存并明确标识该快照，不能冒称旧提交。

```bash
python3 tools/skill_lens.py inspect \
  --source-dir /path/to/target-skill \
  --case-id my-skill --revision ACTUAL_SOURCE_REVISION \
  --auto-providers --timeout-seconds 60 \
  --out /path/to/results/my-skill
python3 tools/skill_lens.py diagnose /path/to/results/my-skill
python3 tools/skill_lens.py visualize /path/to/results/my-skill --output /path/to/results/report.html
python3 tools/skill_lens.py ask /path/to/results/my-skill "output format" --limit 5
python3 tools/skill_lens.py mechanism /path/to/results/my-skill \
  --question "how does it adapt to the intended audience?" \
  --output /path/to/results/mechanism-prompt.txt
python3 tools/skill_lens.py apply-mechanism /path/to/results/my-skill \
  /path/to/results/model-analysis.json
python3 tools/skill_lens.py review-mechanism /path/to/results/my-skill \
  /path/to/results/model-analysis.json \
  --output /path/to/results/mechanism-review.json
python3 tools/skill_lens.py impact /path/to/results/my-skill \
  --path scripts/main.py --max-depth 2
python3 tools/render_bundle_explanation.py /path/to/results/my-skill \
  --source-root /path/to/target-skill --output /path/to/results/explanation.md
```

运行输出与缓存放在被分析源码目录之外。Provider 会读取源码树中的文件，
包括 `node_modules`；分析前选好目标范围，不必把整个工作目录作为输入。
移动到另一台机器时，六文件 Bundle 可离线读取；源码链接需要该机器上的
源码目录。源码不在 Bundle 内，不能假定另一台机器上原来的绝对路径存在。

## 如何读结果

- Capability / Component / Scenario：能力、组成与场景的证据关联。
- Limitation / Uncertainty / diagnostics：限制、未验证项与缺失的语言覆盖。
- `trace` / `impact`：静态关系切片，不是运行录像。
- 规则 Projection 可能有数千个对象；先按问题检索，再看引用。
- HTML 报告可筛选解释、展开原文证据、查看引用图与原始源码关系；不会推断新的语义执行流程。
- 双语报告同时显示英文原文与中文理解；术语可在报告开头集中对照，代码标识符与路径保持原样。
- 有 `instruction-analysis.json` 时，HTML 默认先展示机制、受众与表达规则；
  “核对源码”再查看原始文件和 Graph 关系。较弱模型可用 `mechanism` Prompt
  生成候选分析，但必须先经过 `apply-mechanism` 的 Evidence Gate；发布前可用
  `review-mechanism` 查看引用覆盖和仍需人工判断的边界。

模型解释可以经外部 Projector 与 Evidence Gate 接入，但需要另行配置模型
服务，且对象划分仍不稳定。消费已有 Bundle 无需账号或 API key。

## 发布边界

开发仓库的 `research/`、`evals/`、第三方源码 checkout、模型实验记录与
本地输出不属于默认发布范围。使用 `tools/build_alpha_distribution.py`
生成允许列表候选，再对候选审核和提交；不要在开发仓库直接 `git add .`。
候选的 `release-manifest.json` 记录每个文件的 SHA-256。
发布者尚未选择项目许可证；公开发布前应确定授权方式。
当前已在本机的重定位目录验证，其他操作系统仍需实机验证。
