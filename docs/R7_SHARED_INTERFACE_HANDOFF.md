# R7 第一切片：整理已有共享生成接口

前置：R1–R6 的本轮切片已独立签收，见 Backlog 与各复审记录。两工作区仍有此前未提交维护。此次只整理后端已有共享能力；完成后交独立复审。

## 问题与已有能力

`backend/app/structure.py` 目前仅通过 `artifacts._fact_payload_from_facts`、`artifacts._fact_batches`、`artifacts._run_json_step` 依赖整个产物模块。两个真实调用方都需要已提升事实的输入投影、有限批次和可恢复的 JSON 步骤。这组 interface 应有明确归属，使修改共享步骤时无需进入卡片生成实现。

`llm.py` 已负责模型通信、JSON 格式与通信重试，`planning_transport.py` 已负责可逆标识符映射，`storage.py` 已有持久化步骤和尝试记录。保留这些职责，不新增并行任务引擎、队列、插件注册表或另一套模型适配层。

## 允许的实现范围

1. 将事实输入投影及批次预算、持久化 JSON 步骤整理为少量明确的共享 module/interface，两个调用方直接使用。可按事实输入与持久化步骤两种职责分文件；命名由实际实现决定。共享 module 不反向导入 artifacts 或 structure，不复制一份执行器，也不留下只为转调而存在的重复 facade。
2. 将产物特有的 `PlanCoverageError`、覆盖补全与全局计划语义留在产物实现。现有 `_run_json_step` 不只是通用重试：它会复用已保存的 global_plan 响应、按缺失事实做有界补全，并在补全失败时避免整份计划重写。提取时用最小且显式的调用方接缝保留这些行为，不把产物异常名或 stage 特判藏进新的共享 module。
3. 清楚表达步骤实际只需的任务身份（id、model_id、fake_model），兼容现有 ArtifactDraftJob 和 StructureDraftJob 的真实调用，避免把全部业务模型或可选配置复制进新 interface。说明错误与副作用即可，不必制造通用类型体系。
4. 事实排序、任务事实选择、提示内容、卡片/章节校验、任务状态/锁与最终写入继续归各自调用方。迁移相关测试的导入或 patch 位置时，保持原有行为断言，并让共享行为通过新的真实 interface 验证。

## 必须保持的行为

- 原始事实 ID、来源页、证据状态和字段投影不变；model_candidate 不作为已提升事实输出。现有输入顺序、字符/token/数量预算与单条超限行为不变，不能截断、漏事实或将批次变成产品准备单元。
- 步骤 hash 的序列化、有效模型身份、stage/index 与 ID 生成保持不变；已有成功步骤仍可命中，不因搬文件而失效。复用仍与任务、模型和输入绑定，输入字典与原消息不被就地修改。
- 尝试记录、状态、原始响应、校验输出、错误截断与异常传播不变。无响应的通信失败不新增业务层循环；普通确定性校验最多一次修正，原请求超时和 token 上限保留。
- global_plan 的缺失覆盖补全、保存响应恢复、补全失败保留原计划，以及 structure 的缺失分配恢复，均保持原有请求次数和归属约束。特别检查补全子步骤与父步骤的缓存及重试，不能重复消耗成功请求。
- 不修改提示版本、内容语义、schema、数据库表、任务/端点契约、原子发布、准备选择或前端。两板块与规则边界保持不变，不接入新模型，不读取正式业务正文。

## 验收与交回

先列出现有三个私有函数的调用和依赖，再说明最终 interface、复杂度归属和删除了哪些跨模块私有依赖。提交实际可运行的提取结果，不只写建议。若为保留真实错误/恢复契约必须调整接缝，在交回说明具体依据，避免顺手改运行语义。

用合成事实、Fake/计数 client 与临时存储验证公共 interface：输入投影/批次不丢失，成功缓存不重复调用，改模型/输入隔离，校验修正有界且尝试留存，无响应失败及时停止。对提取涉及的覆盖恢复行为保留集成验证，不能只 mock 掉新 interface 便声称工作流通过。新增检查应保护这些不变量，不镜像实现细节。

至少重跑 `test_artifact_job_state.py`、`test_artifact_shape_normalization.py`、`test_chapter_planning_budget.py`、`test_plan_coverage_recovery.py`、`test_structure_generation_workflow.py`、`test_structure_assignment_recovery.py`、`test_structure_disposition_contract.py`、`test_structure_job_cleanup.py`、`test_artifact_workflow.py`、`test_fantasy_artifact_workflow.py`、`test_fantasy_long_artifact_workflow.py`、`test_artifact_semantic_prompt_contract.py`、`test_state_anchor_contract.py`、`test_rule_reference_boundary.py`，以及 `test_llm_retry.py`、`test_llm_null_content.py`、`test_json_format_retry.py` 的通信层基线与现有运行 Node 回归。完成 ruff、语法与差异检查。Python 外层设置临时 DB_PATH/UPLOAD_DIR，防止旧脚本恢复路径后的 finally 触及正式库。

更新 audit/backlog，写明实测与限制。保持其他未提交内容；不提交、推送、发布，不动正式库。此工程切片通过不代表真实产物质量或备团时间目标已通过。
