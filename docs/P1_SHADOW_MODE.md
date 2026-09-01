# P1 LLM 影子模式与候选复核

状态：**历史组件记录。P1 的隔离任务、离线 diff 和复核边界继续有效；R1 已接通真实候选生产，R2 已增加独立、显式 promotion。当前路线见 `REASSESSMENT_2026-08-26.md`。**

P1.1 是一个隔离的控制面。它保存模型候选及其失败历史，没有任何直接路径把候选写进 `ExampleBundle.plans`、批准卡片或 `SessionState`。候选始终保持 `model_candidate`；只有 R2 的第二步显式 promotion 可以把 accepted 候选复制成独立 `SourceFact`，不会修改或删除原候选。

## 数据契约

每个影子任务固定以下可复现输入：

- 源文件、源文件版本和页码集合；
- 目标规则档案；
- 模型、prompt 与 schema 版本；
- 幂等键和本地输入摘录。

每次尝试都会追加独立运行记录，保存开始/结束时间、尝试序号、模型/prompt/schema 版本、候选数量、原始响应摘要、解析错误或传输错误。成功结果会产生单独候选，每条候选必须：

- 使用 `model_candidate` 证据状态；
- 引用任务绑定的同一源文件和页范围；
- 继承任务的源文件版本；
- 停留在 `needs_review` 队列。

截断 JSON、无效字段、来源文件或页码越界会把这一次运行标为 `failed`，不会保留半份候选。失败任务可用相同任务重新提交下一次结果；取消任务则拒绝后续运行。

## API

```text
POST /api/domain/shadow/tasks
GET  /api/domain/shadow/tasks
GET  /api/domain/shadow/tasks/{task_id}
POST /api/domain/shadow/tasks/{task_id}/runs
POST /api/domain/shadow/tasks/{task_id}/cancel
GET  /api/domain/shadow/review-queue
POST /api/domain/shadow/candidates/{candidate_id}/review
POST /api/domain/shadow/review/batch
POST /api/domain/shadow/candidates/{candidate_id}/promote
```

创建任务的相同幂等键会返回原任务；若同一键携带不同源范围或版本，接口返回冲突而不覆盖已有记录。`runs` 接口接收二选一的 `raw_response` 或 `transport_error`，因此模型网关、离线批处理或测试夹具都可以把结果安全地交给队列。该接口当前**不会自动请求任何外部模型**。

## 存储边界

SQLite 使用独立的 `shadow_tasks`、`shadow_runs` 和 `shadow_candidates` 表。运行完成时，任务状态、运行记录和候选会在同一事务中保存；模型运行本身不会读取或修改领域包与桌边状态。显式 promotion 另写 `candidate_promotions` 审计快照和任务独立书架，不改变影子候选。

## 离线验证

```powershell
python scripts/test_shadow_mode.py
```

该回归在临时 SQLite 数据库中执行，覆盖幂等重放、冲突键、JSON 截断、来源越界、传输失败、失败后重试和取消。它还断言 `naimen_pilot` 的领域覆盖层与运行状态保持为空。

## P1.2 候选 diff

`scripts/diff_shadow_candidates.py` 把 `needs_review` 候选和 P0.2 人工金标准做纯离线、稳定的比较。基线包含 `naimen_pilot` 的 20 条确认事实、人工包的 PDF p159-165 范围，以及确认事实明确声明的关系；报告不会调用模型，也不会接受、拒绝或改写候选。

报告固定输出以下五类复核信号：

- 漏项；
- 无依据页或版本；
- 错误页码；
- 疑似错误合并；
- 疑似过度摘要。

同时会单列类型不一致、未识别关系和未匹配金标准的候选。文本匹配只使用保守的确定性字符重叠；精确覆盖应由候选的 `possible_links` 指向确认事实 ID。JSON 报告含人工基线和候选快照的指纹，因此旧报告不依赖之后的模型版本。

使用保存的 API 形状快照：

```powershell
python scripts/diff_shadow_candidates.py --candidates data/fixtures/naimen_shadow_candidate_diff_fixture.json --write-json data/light_results/naimen-shadow-diff.json --write-markdown data/light_results/naimen-shadow-diff.md
```

使用真实影子任务的复核队列（只读 `shadow_*` 表）：

```powershell
python scripts/diff_shadow_candidates.py --task-id shadow_task_example --write-json data/light_results/shadow-task-diff.json --write-markdown data/light_results/shadow-task-diff.md
```

离线回归：

```powershell
python scripts/test_shadow_candidate_diff.py
```

## P1.3 候选复核 API 与工作台

工作台的“候选复核”页按任务、复核状态和来源页筛选候选。GM 可以查看候选关联的原文页预览，直接编辑当前候选内容，随后接受、拒绝或退回复核；多条候选也可以批量标记并附共同说明。界面始终同时显示“模型候选”与当前复核状态，证据等级不只依赖颜色表达。

单条复核请求接受 `review_state`，并可附带当前 `text`、`content_basis` 与 `review_note`：

```text
POST /api/domain/shadow/candidates/{candidate_id}/review
```

批量请求接受最多 100 个唯一候选 ID、统一的 `review_state` 以及可选的 `review_note`：

```text
POST /api/domain/shadow/review/batch
```

队列默认只返回 `needs_review`，也可按 `accepted`、`rejected` 或 `all` 查询，并可用 `task_id` 缩小范围：

```text
GET /api/domain/shadow/review-queue?task_id=shadow_task_example&review_state=all
```

候选是当前记录：编辑会覆盖 `text`、来源和相关字段，并将状态退回 `needs_review`。复核历史只记录动作、说明、字段/来源变化、关联 ID 和时间，不保存旧正文或旧字段。拆分/合并直接替换当前候选集合，结果重新待复核。刷新或重新打开队列后会从 SQLite 的独立 `shadow_candidates` 表恢复。即使状态为 `accepted`，候选仍是 `model_candidate`，不会自动写入任何运行资产。GM 必须再明确选择 `source_fact`、`inference` 或 `gm_authored` 执行 promotion；未提升候选仍不能被卡片或场景计划引用。

离线回归：

```powershell
python scripts/test_shadow_review.py
```

## 下一步

复核页的当前记录编辑、拆分/合并和显式 promotion 主链已接通。后续仍可补齐实体/场景聚类与冲突标记，但不得重新引入旧的双正文候选版本模型，也不能跳过事实质量控制去扩展规则档案。
