# PR 收尾：生成可靠性、规划预算与来源归属完整性

本文件是本 PR 的最终验证记录与正文草稿。范围只含生成链路的可靠性、规划预算与
brief／chapter brief 的来源归属完整性，以及身份判定与素材化的来源忠实性；监狱地点入口、无来源的位置与条件、整章稳定性、
奇幻真实质量、真人备团效率都不在本 PR 内。

## 最终验证记录

- 回归、lint 与编译检查在**干净候选**上运行：从 `HEAD` 的受版本控制内容导出，再只覆盖本 PR 的精确 16 文件清单。
  构建清单及逐文件 SHA-256 留在本地审查证据中；候选不携带本机数据库、`output/`、`.venv` 或其它工作树。
- 入口：`python scripts/run_isolated_test.py <scripts/test_*.py>`。该 runner 在导入应用之前把
  `storage.DB_PATH` 指向临时文件，并把 `LLMClient._worker_call` 换成拒绝哨兵，
  本轮公开回归在该隔离入口下运行，不依赖本机 `data/` 或 `output/`，未发出真实模型请求。
- 通过项：`test_identity_round2`、`test_global_plan_compact_contract`、`test_global_plan_scale_budget`、
  `test_global_plan_window_budget_resume`、`test_materialize_budget`、`test_runtime_brief_closure`，
  以及六个本次修改过的既有测试（semantic prompt 契约、shape normalization、cross-chapter identity、
  fantasy workflow、split recovery、same-scope identity）与现实恐怖 artifact workflow、
  shared generation interfaces、empty reply recovery、local digest split recovery；
  `ruff`、`compileall` 与前端 JavaScript 语法检查通过；`git diff --check` 在源工作树通过。
- 本机历史证据（不在干净候选重现，也不随仓库分发）：有限真实规划窗口与素材化的
  probe 记录、来源冻结导出、以及基于真实 probe 的 brief 重放。后者需要通过
  `--replay-real <probe.db>` 显式指定路径；路径不存在时直接报错，不会静默跳过。

## PR 正文草稿

### 问题

1. 规划窗口的 shared budget 与分窗恢复在真实 97 单元／606 事实范围上会耗尽请求额度，
   且失败后没有明确的续跑入口。
2. 多单元窗口沿用整范围的长契约，导致回复长度越界；概览类素材化卡在固定 9000 输出预算下
   被 `finish_reason=length` 截断。
3. 共享语义指导没有覆盖"资源入口完整性""知识与披露状态分离""每卡自有事实闭包"等职责，
   地点规则对"同一事实里的替代机构"含糊，导致真实输出里出现合并机构、丢失入口、
   把条件写成自动阶梯。
4. `RuntimeBrief` 的 `field_sources` 与 `fact_ids` 不闭合：模型声明的字段来源可以不在自己
   的事实清单里；合并时 `fact_ids` 被静默截到 100 而 `field_sources` 完整保留；
   章节摘要也没有按自己章节的归属校验引用。

### 变更

- **规划窗口与预算**：分窗按真实发送字符计量，多单元窗口 32k、单叶 12k→16k（每次步骤一次提额），
  共享请求上限 `L = min(64, max(16, B+15))`；长度失败按持久窗口恢复，重入只补缺失窗口。
- **共享规划消息**：root／多单元窗口／单叶共用一份语义指导，新增资源入口完整性、
  知识与披露分离、条件与阈值精度、每卡自有事实闭包等职责；地点规则明确
  "同一条事实里的不同机构各自成卡"和"已说明可进入/可使用的地点保留入口"。
- **素材化预算按卡职责**：`ARTIFACT_OVERVIEW_MAX_TOKENS = 16000` 只给 `chapter_overview`，
  其余卡与 direct generation 保持 9000；预算随步骤 `max_budget` 夹紧，不改消息与缓存身份。
- **来源归属闭包**：新增 `_brief_closure` / `_close_plan_briefs`，在规划输出消费边界
  （新回复、根与窗口缓存、写 bundle 前）把 brief 已声明但漏列的事实并入自身清单，
  未知／越范围／跨章节引用明确失败；章节摘要按自己章节的 `adventure_chapters[].fact_ids`
  校验；移除合并时的静默截断，超过 100 项显式失败。
- **测试设施**：新增 `scripts/run_isolated_test.py`，让公开回归不依赖本机数据目录。
- **身份与来源忠实性**：身份候选携带完整事实依据，未判定或可信多人报告不冒充单一实体成功；
  素材化保留来源限定语，无来源字段的空引用不被整卡引用回填。

### 验证

- 离线：上节列出的 16 个脚本在干净候选上全部通过，`ruff`／`compileall` 与前端语法检查通过；
  `git diff --check` 在源工作树通过。
- 有限真实调用（历史证据，非本 PR 的重现项）：存在成功、长度截断和结果未知的请求。
  部分素材化试验超出约定调用次数，另有零调用任务误发两次、其中第二次结果未知的事故；
  原始 probe 与账本留在本地，不据此宣称整章通过或所有试验均遵守上限。
- 未证明：整章端到端、26 个窗口的整体成功率、全部卡片的语义正确性、identity／materialize
  之外的下游质量。

### 已知限制

- `RuntimeBrief.fact_ids` 上限 100：合法但并集超过 100 的多窗口 brief 合并会**显式失败**，
  不再静默截断；需要拆成独立且不再合并的备团范围或另行决定上限，单纯拆窗不能解除并集上限，本 PR 不扩 schema。
- 素材化概览卡在 16000 下完成过，但这是候选值，不是稳定预算结论；地点卡是否在 9000 下
  稳定完成尚未证明（同一输入曾一败一成）。
- 元数据闭合不等于语义正确：无来源的"现场／前额"位置、条件限定等质量问题留待后续。
- 新新监狱等"可访问场所缺独立地点入口"仍属已知规划问题。

### 状态

PR #2 仍为 **OPEN**（未合并），head `bc7afb9`，base `main` `8dc2ea6`。
本 PR 的候选改动尚未提交或推送。
