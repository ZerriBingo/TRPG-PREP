# Agent Skill 调研：VoltAgent 与 Matt Pocock

日期：2026-08-28

本次调研只做目录与流程评估，不安装第三方 Skill，也不修改现有开发代码。当前项目已暂停维护，后续应先恢复流程稳定性，再考虑引入。

## 来源

- VoltAgent/awesome-agent-skills：面向 Claude Code、Codex、Gemini CLI、Cursor 等工具的 1000+ Skill 目录，强调来自真实工程团队的 Skill，而不是批量生成内容。citeturn0search0
- mattpocock/skills：完整的 AI coding workflow。其 Codex 安装方式是 `npx skills@latest add mattpocock/skills`，可选择具体 Skill；安装后建议运行 `/setup-matt-pocock-skills`。citeturn0search1
- `setup-matt-pocock-skills` 会记录 issue tracker、triage 标签和文档目录，输出到 `docs/agents/`；它是显式调用的 setup Skill，不会被其他 Skill 自动触发。citeturn0search3

## 最值得未来引入的能力

### 1. `setup-matt-pocock-skills`

优先级：高，但应等项目准备进入 Git 维护阶段。

当前仓库缺少 `.git` 固定点和 `docs/agents/issue-tracker.md`，这正是现有 `code-review` 无法执行正式流程的原因。setup Skill 可以先把 issue tracker、标签和文档目录固定下来，再让 review、triage 类 Skill 读取这些约定。它不会自动替用户做决策，必须显式运行并确认。

### 2. `codebase-architecture` / 架构梳理类 Skill

优先级：高。

本项目目前同时存在旧 campaign 链路、domain workbench、prep job、artifact job 和运行模式。架构梳理 Skill 可在继续开发前生成模块边界、数据流和淘汰候选，适合解决“维护混乱”而不是继续堆功能。

### 3. `grill-with-docs` / 设计质询类 Skill

优先级：高。

现有 `grilling` 能进行决策追问，但更需要结合仓库事实、现有文档和已确认约束。类似 `grill-with-docs` 的能力可以把问题限定在真实代码和文档上，减少重复询问与方向漂移。社区讨论也把 `codebase-architecture`、`grill-with-docs` 和 UI mockup 视为一套较完整的前置工作流，但这属于社区经验，不是仓库官方承诺。citeturn0search7

### 4. `ui-mockups` / 结构化 UI 设计类 Skill

优先级：中高。

事实网、运行模式、场景计划编辑器都还缺少锁定的交互规范。先用 UI mockup Skill 确认桌面/移动端布局，再实现关系图、地点导航和结构化编辑，会比直接修改现有原生 JS 更稳。

### 5. `triage` / issue 管理类 Skill

优先级：中。

适合在 Git 和 issue tracker 建立后使用，将“补卡失败”“计划覆盖不足”“规则名泄漏”“默认书架选择”等问题拆成可追踪条目。当前没有 issue tracker 配置，暂不应安装后直接运行。

### 6. 任务专用测试/调试 Skill

优先级：中高。

VoltAgent 目录应优先筛选 e2e、Playwright、regression、debugging、data-migration、SQLite 等主题，而不是 UI 装饰或营销文案。其目录规模很大，条目来自多个团队，必须逐个读取 `SKILL.md`、脚本和权限声明后再安装。目录本身明确是精选集合，不等于每个条目都经过本项目适配或安全审计。citeturn0search0turn0search4

## 暂不建议引入

- 自动重构、自动迁移或“全仓库重写”类 Skill：当前领域边界和 profile 命名尚未稳定。
- 自主部署、外部发布、自动提交 PR 类 Skill：用户尚未要求自动外发，且当前项目仍处于本地测试阶段。
- 依赖大量外部服务的 agent orchestration Skill：本项目主要问题是本地数据模型、LLM 任务恢复和 UI 工作流，不是多代理编排。
- 未经审查的社区 Skill：Skill 应像代码一样审查；研究文献指出生态存在仓库劫持、恶意或误报分类等风险，不能因为目录收录就默认可信。citeturn0academia16

## 推荐的未来调用顺序

1. 先完成 Git 初始化、`CONTEXT.md`、`docs/agents/issue-tracker.md` 和现有维护记录整理。
2. 显式运行 `setup-matt-pocock-skills`，确认项目级 tracker、标签和文档目录。
3. 调用架构梳理 Skill，产出旧链路、domain 链路和持久化边界图。
4. 用 `grill-with-docs` 重新审查场景覆盖、补卡、事实网和运行模式的产品决策。
5. 用 `ui-mockups` 固定关系图、地点导航和结构化编辑器的交互稿。
6. 再引入测试/调试/迁移类 Skill，并给每个 Skill 建立来源、权限、适用范围和回滚记录。
7. 最后才恢复补卡与完整章节真实上游验收。

## 路由结论

这些外部 Skill 不应全部自动加载。推荐采用“任务意图 -> 候选 Skill -> 读取完整 SKILL.md -> 人工确认适用范围 -> 执行 -> 记录结果”的路由。尤其 `setup-matt-pocock-skills` 官方说明它不会由其他 Skill 自动触发，因此必须在项目进入 Git 维护阶段时显式调用。citeturn0search3
