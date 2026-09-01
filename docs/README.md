# 开发与维护文档

这里是维护者和开发协作者的入口，不是产品使用教程。GM 用户请从仓库根目录的 [README.md](../README.md) 开始。

## 读者与入口

| 读者 | 从哪里开始 |
| --- | --- |
| GM 用户 | [根目录 README](../README.md)：安装、启动、模型连接和现实恐怖 Beta 工作流。 |
| 维护者 | 本页，然后阅读当前方向、Backlog 和相关契约。 |
| Coding Agent | [项目上下文](../CONTEXT.md)，再按任务读取 `docs/agents/` 与下面的当前文档。 |

## 当前文档

1. [当前产品方向](./REASSESSMENT_2026-08-31.md)：三板块边界、现实恐怖地点卡和运行模式。
2. [项目上下文](../CONTEXT.md)：稳定术语、工程规则和已确认的产品判断。
3. [实施 Backlog](./IMPLEMENTATION_BACKLOG.md)：下一步、验收闸门和停止条件。
4. [产物契约](./ARTIFACT_CONTRACTS.md)：各板块生成什么，以及不生成什么。
5. [产品护栏](./PRODUCT_GUARDRAILS.md)：来源追溯、模型权限和人工复核边界。
6. [LLM 管线](./LLM_PIPELINE.md)：任务、尝试、失败恢复和原子写入。
7. [模型提供商接入调研](./RESEARCH_2026-09-01_MODEL_PROVIDERS.md)：README 中外部模型接入措辞的官方依据。

## 文档分类

- `REASSESSMENT_*.md` 与 `ADR-*.md`：产品或架构决策；新决策应明确记录日期、动机和影响面。
- `ARTIFACT_CONTRACTS.md`、`PRODUCT_GUARDRAILS.md`、`LLM_PIPELINE.md`：当前执行约束。
- `IMPLEMENTATION_BACKLOG.md`：待办和验收状态，不是用户文档。
- `MAINTENANCE_*.md`：维护过程和验证记录，用于追溯，不自动覆盖当前契约。
- `RESEARCH_*.md` 与 `RESEARCH_NOTES.md`：研究证据和外部资料笔记。
- `agents/`：供 Coding Agent 使用的辅助上下文与流程说明。

## 发布与公开仓库

- [发布记录](../CHANGELOG.md) 是面向外部读者的版本摘要。
- `data/`、`output/` 和 `Resource/` 属于本机运行或参考资料，不应作为公开发行内容。
- 项目代码许可证见根目录 [`LICENSE`](../LICENSE)；公开发布时仍需确认第三方素材权利和测试范围。

## 历史资料

2026-08-31 之前的重评、维护记录和重做简报均保留用于追溯。阅读历史文件时，以“当前文档”中的方向和契约为准。
