# 2026-08-26 跨页边界、任务删除与候选提升维护记录

## 触发反馈

GM 已确认 PDF 上传和真实模型抽取基本可用；一次末段失败经重试完成。随后暴露三个工作流问题：

- 固定页数重叠只能切运输窗口，不能证明地点、人物或场景的语义边界。
- 备团任务会持续堆积，没有删除命令。
- `accepted` 候选仍停在影子表，书架只显示种子夹具，无法继续生成下游产物。

## 切分结论

纯机械算法不能保证语义段落完整。本轮不再把窗口显示或描述为内容分段，而是建立以下可检查约束：

1. GM 选择的 `PrepScope/PageSpan` 仍是唯一备团范围。
2. 每个选中页恰好属于一个 `core_span`；候选以最早引用页作为锚点，只能由拥有该页的窗口保存。
3. 每个内部核心边界的相邻页会作为 `context_pages` 被两侧窗口重复读取。典型边界 `p4 | p5` 会让前窗读取到 p5、后窗读取到 p4。
4. 核心窗口最多 3 页，并结合输入字符预算和页首标题提示自适应收束；页首标题只是启发，不是语义证明。
5. 前置上下文优先保留页尾，后置上下文优先保留页首；核心页超预算时保留页首与页尾。
6. `boundary_basis`、`boundary_pages`、`context_pages`、`truncated_pages` 全部持久化并显示在任务卡上。
7. 模型若在相邻窗口重复返回同一跨界事实，服务端按最早来源页执行核心归属过滤；原始响应摘要仍保存在 run 中。

这能保证页级覆盖、边界上下文和候选归属可审计，不能保证以下内容：

- 跨越超过一个上下文页的长章节结构；
- OCR 缺失、错序或扫描页没有文本层；
- 模型遗漏引用、错误理解实体关系或把多个事实错误合并。

因此精确 `source_refs` 和 GM 复核仍是事实进入书架前的必要边界。旧任务不重新切分，显示为 `legacy-overlap-v1/旧窗口`；当前新任务使用 `core-context-v3`，并由 semantic-v2 先决定语义段，再生成传输窗口。

## 任务删除

新增 `DELETE /api/domain/prep/jobs/{job_id}`：

- 运行中的任务禁止删除，必须先取消并等待当前请求结束。
- 删除在一个 SQLite 事务中清理 `prep_jobs`、关联 `shadow_tasks`、`shadow_runs` 和 `shadow_candidates`。
- 已经显式提升到书架的事实和 promotion 审计快照保留；删除任务不会破坏已经确认的工作成果。
- 尚未发生提升时不会预先创建空书架，因此删除失败或废弃任务不会留下空工作区。

## 显式提升与书架

每个备团任务拥有稳定 `workspace_id`，但工作区只在首次提升时创建。流程保持两步：

`needs_review -> accepted -> promote(source_fact | inference) -> SourceFact`

约束：

- 只有最新复核状态为 `accepted` 且存在复核历史的候选可以提升。
- 同一候选重复以同一证据状态提升是幂等操作；改用另一证据状态会返回冲突。
- 提升复制完整 `source_refs/source_version`，使用候选当前记录；编辑后的非原文内容必须标为推断或 GM 创作。
- `SourceFact.provenance` 保存 candidate、task、run、review 和提升时间。
- `candidate_promotions` 保存候选与事实完整快照；即使原任务随后删除，审计信息仍在。
- 工作区 ID 与任务 ID 一致，不会把真实上传模组注入 `red_signal_fixture` 或 `naimen_pilot`。
- 接受不自动提升，提升也不自动生成卡片或场景计划。

工作台增加单条“提升为原文事实 / 可验证推断”和批量“提升已选为原文事实”。接受后界面自动切换到“已接受”列表；批量操作仍要求用户先完成独立的批量接受，再明确执行提升。

## 验证

- `python scripts/test_prep_job.py`
  - 核心页唯一归属与相邻边界双侧读取。
  - 上下文候选按最早引用页去重。
  - accepted 候选显式提升、完整多页来源、provenance 和幂等冲突。
  - 动态工作区可加载；删除任务后书架事实保留，影子行清理。
- `python scripts/test_shadow_mode.py`
- `python scripts/test_shadow_review.py`
- `python scripts/test_shadow_candidate_diff.py`
- `python scripts/test_evidence_status.py`
- `python scripts/validate_domain.py`
- `node --check frontend/workbench.js`
- `python -m compileall backend/app backend/domain`

正式服务在 `http://127.0.0.1:8000/` 验证：旧任务显示删除入口和旧窗口标记；候选复核加载 79 条真实候选；桌面 1280px 无横向溢出、控制台无错误。未替 GM 接受或提升正式候选。
