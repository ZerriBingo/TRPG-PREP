# 收口回执：身份、来源忠实度、页面备注（2026-11-02）

本文件是 A–D 的执行回执，交 Codex 复审。它只陈述做过什么、证据在哪里、还没有证明什么；
PR 尚未创建，分支未推送、未合并，也没有改版本号、标签或打发布包。

## 1 批次与基线

- 干净候选：`E:/France/Coding/pr-closeout-candidate`，分支 `codex/pr-closeout-2026-11-02`，
  从 `origin/main = 8dc2ea6a5a928c1453a8b2d22d242ea8f9993026` 新建。
- 维护工作区 `E:/France/Coding/trpg-prep/.claude/worktrees/cranky-mendeleev-676227` 的全部改动、
  暂存与证据原样保留；本候选是逐文件迁移，不是整分支推送。
- 正式库 `E:/France/Coding/trpg-prep/data/app.db`，SHA-256 仍为
  `47c02cbf5e55d2d196a5afdca690ae7885cf98e5ea980788a39d618b541643e3`（与开工 manifest 一致）。
  本轮未改正式配置、未重启正式服务、未跑正式生成任务。

## 2 迁入候选的文件

后端（6）：`backend/app/artifacts.py`、`fact_input.py`、`llm.py`、`llm_worker.py`、
`persistent_step.py`、`planning_transport.py`。

前端（2）：`frontend/workbench.html`（备注面板加显式类名）、
`frontend/workbench.css`（本轮新增进清单：把相邻选择器隐藏改为显式归属）。

既有测试（7）：`test_artifact_semantic_prompt_contract.py`、`test_chapter_planning_budget.py`、
`test_fantasy_prompt_contract.py`、`test_handout_coverage.py`、`test_reasoning_effort.py`、
`test_rule_reference_boundary.py`、`test_shared_generation_interfaces.py`。

新回归（5）：`test_empty_reply_recovery.py`、`test_global_plan_split_recovery.py`、
`test_handout_narrative_use.py`、`test_local_digest_split_recovery.py`、
`test_same_scope_identity.py`（本轮新增）。

文档（3）：`docs/IMPLEMENTATION_BACKLOG.md`、`docs/LLM_PIPELINE.md`（均改写了过期的当前状态）、
本回执。两份长回执（`LIVE_TRIAL_2026-11-01_GLOBAL_PLAN_IMPLEMENTATION.md`、
`LIVE_TRIAL_2026-11-02_ROUND23_RECEIPT.md`）**不进入**本 PR，保留在维护工作区。
`CHANGELOG.md`、`README.md`、`docs/RELEASE_0.1.6a0_CLOSEOUT.md` 与 `docs/README.md` 也未带入：
它们是 0.1.6a0 的历史修订，需要单独核对历史准确性，不由本轮代码收口顺带提交。

## 3 A：同范围身份与单人 NPC

改动（`backend/app/artifacts.py`）：

1. `_identity_candidate_components` 增加第二路候选：同一备团范围（章节集合相同）、同一卡型
   （地点/NPC/威胁）、且引用至少一条共同事实的卡片进入同一候选组。原有的“跨章节共享剧情线或
   运行组”路径逐字保留，只有走这条新路径的组才免于“至少跨两章”的过滤。
2. 候选载荷为每张卡附上其事实闭包的有界切片（最多 8 条、每条 240 字），并进入步骤输入，
   因此事实改写会让对应判定失效。
3. 身份重整提示改为 v2：以“同一人物／同一地点／同一具名威胁”为单位；共同事实、同场景、
   互相介绍都不构成同一实体；不得把两位可分人物放进同一簇。
4. `_merge_match` 收紧：标题匹配只在双方都没有身份键、或身份键相同的时候成立；
   身份判定为不同实体的两张卡不再因为同名被并掉。
5. 规划提示（整范围与叶子）加入 `PERSON_SEMANTIC_GUIDANCE`：一张 NPC 卡只承载一位可扮演人物，
   两人合成一张卡不是 NPC 卡。素材化提示同样加入该约束。
6. 版本：`GLOBAL_PLAN_PROMPT_VERSION` v21→v22，`IDENTITY_RECONCILE_PROMPT_VERSION` v1→v2；
   `LOCAL_DIGEST_PROMPT_VERSION` 与规划索引不变（局部整理不因此失效）。

离线反例（`scripts/test_same_scope_identity.py`，8 项检查全绿）：

| 检查 | 断言 |
| --- | --- |
| 无章节板进入重整 | 三个候选组精确为 `[cult,cult] [elias,elias] [hotel,hotel,publisher,publisher]`，脚本化模型判定后最终只剩 4 张卡；酒店与出版社共享 `fact_shared` 仍各自成卡 |
| 两人不合一 | 艾丽卡、约拿、两人合卡同时进候选，判定全为单例后仍 3 张卡；两张人物卡各自只带自己的 1 条事实 |
| 身份否决同名 | 两个不同实体同名“书房”各自带不同身份键 → 不合并（2 张）；无身份键时旧标题规则不变（1 张） |
| 请求有界且可缓存 | 140 张候选分块发送，每块卡数 ≤120、字符 ≤ 预算 + 该块首组；候选全覆盖；再进入 0 次请求 |
| 超限只报告 | 超过 `MAX_IDENTITY_CANDIDATE_PLANS` 的组不发请求、不截断，写入“未重整”说明 |
| 事实改写失效 | 改写一条事实后重新判定，且只重发读过该事实的那一块 |
| 失败有界 | 每块只尝试一次，无重复请求，问题摘要记录失败 |
| 跨章节路径仍在 | 共享运行组的两章地点仍进候选并合并 |

红灯对照（`output/pr-closeout-20261102/red_identity.py`，对同一份 fixture 跑旧规则）：

```
fixed candidates: [['p_cult_a', 'p_cult_b'], ['p_elias_a', 'p_elias_b'], ['p_hotel_a', 'p_hotel_b', 'p_publisher_a', 'p_publisher_b']]
old candidates:   []
RED reproduced: the old rule nominates no candidate, so nothing can merge
```

## 4 B：来源忠实度与一次受限真实检查

改动（`backend/app/artifacts.py`）：

1. 查清的冲突：素材化提示原文要求“每个必填字段 MUST be present and non-empty”（旧大写强调），
   并规定“来源未确立时说明材料假定什么，写进 assumed_state”。两条叠加，模型就把没有来源的
   动机、交流条件、访问规则写成了“材料假定”。共享的 `SOURCE_SEMANTIC_GUIDANCE` 也有
   “state what the material assumes” 一句，但它同时被局部整理与规划消费，故本轮**不改**它，
   避免局部整理与规划索引整体失效。
2. 新增 `MATERIALIZATION_FIDELITY_GUIDANCE`（只进素材化提示，`PROMPT_VERSION` v14→v15）：
   必填 ≠ 有来源，来源没有就在字段里直说缺口且不需要 `field_sources`；工作假设只描述材料开始
   时的状态，不得补出动机、交换条件、访问规则、接待流程或员工态度；来源的限定语必须保留，
   不得改写为断言，也不得补出限定语留空的具体细节；一张 NPC 卡只写一个人的既定目标；
   一个地点的卡只写来源说过的抵达、在场与使用。
3. 必填字段那句改为“must be present and non-empty … name that gap inside the field instead of
   inventing detail”，并把工作假设唯一收进 `assumed_state`（有该字段的卡型）。

受限真实检查（`output/pr-closeout-20261102/b_fidelity.py`，隔离副本 + 生产客户端/传输/校验）：

- 范围：从 13 条已复核事实里按事实文本取出 4 条子范围（切尔西酒店、普洛斯佩罗出版社、
  电报、艾丽卡+约拿同源页），即“同实体异名、不同实体共享来源、两位人物”都能出现的最小切片。
- 预算与实耗：上限 8 次请求 / 720 秒；**实耗 8 次 / 213.6 秒**；时钟在第一次上游请求时才启动。
- 阶段：局部整理 1 → 规划窗口 2（含一次叶子上行 2 次）→ 身份重整 2 → 素材化 3 成功（4–6 失败）。
- 结果：`job_status=failed`，6 张计划卡里 3 张落地、3 张因**达上限**未发请求（错误原文：
  “本次实测预算已用尽：已发 8/8 次请求，剩余 507 秒，不足以再发一次请求”）。
  按交接“额度耗尽即停止，不开启第二轮”，本轮没有追加请求，也没有放宽预算。
- 记录：`report.json` 含每次请求的 tag、字符数、`prompt_sha256` 前 12 位、`max_tokens`、
  超时、耗时、事实 ID 范围与三个提示版本；不含密钥，也不保存 reasoning 正文。

已落地 3 张卡的语义抽查（人工比对来源，不只看引用 ID 合法）：

| 卡 | 抽查点 | 结果 |
| --- | --- | --- |
| 开场电报与纽约联络总览（chapter_overview） | 电报是否被确定化 | 保留限定语：“电报看起来是从海上船上发出的”“来源只说它看起来从海上船上发出；具体内容、船名与发报缘由未说明” |
| 切尔西酒店（location） | 是否补出访问规则/接待流程 | 明确写“酒店内部、人员、房间安排及他当前是否入住均未说明”“来源没有描述抵达酒店的方式、地址、入口或接待流程” |
| 普洛斯佩罗出版社（location） | 是否与酒店混为一处 | 两张地点卡分开，“不要把切尔西酒店与出版社合并成同一地点” |
| 艾丽卡 / 约拿 | 是否出现无来源的交流条件（上一轮的 `wants` 问题） | 本批未生成这两张卡（预算耗尽），**未验证** |

## 5 C：备注面板与零模型页面验收

改动：`frontend/workbench.css` 原来用 `.runtime-columns + .panel { display: none; }` 把
“本次运行总备注”整块藏掉，没有任何显式类名；`frontend/workbench.html` 给该面板加
`runtime-notes`，CSS 改为显式规则（`.runtime-notes { display: block; }`），旧控制台
（`runtime-action-grid`、两个导出链接）继续隐藏。保存处理与数据结构未改。

页面验收（**未批准**归档副本 + 拒绝型哨兵服务，端口 8766）：

- 概览复核编辑：改写 `situation` 为“电报看起来是从海上船只发出的”→ 保存为已修改 → 状态转
  “已修改，待批准”；落盘 `fields.situation` 含该限定语。
- 批准分支：只批准 4 张（概览、切尔西酒店、埃利亚斯、艾丽卡），**保留 10 张未批准**，
  其中含重复卡“切尔西酒店：埃利亚斯的纽约落脚点”。
- 组装与运行：组装对话框只列出这 4 张已批准卡；运行模式 `本次准备材料 · 1 张`、
  地点筛选 `按类型浏览 1 张`，重复卡不在运行计划里；人物筛选 `按类型浏览 2 张`，
  已批准但未选的艾丽卡仍可检索。
- 本次准备：加入埃利亚斯 → 保存 → `已保存 21:31 · 1 张`，刷新后仍为 1 张；
  落盘 `prep_selections["cthulhu-dark-2e"] = ["card_generated_npc_094276ee2284f8"]`。
- 运行总备注：输入唯一标记 → 保存运行状态 → `运行状态已保存 21:34`；刷新后 textarea 值与面板
  可见性都保持；落盘 `session_states.notes = "隔离验收测试 2026-11-02 21:34：…"`。
- 零模型：`sentinel_calls.jsonl` 未生成（0 行）；该哨兵在此前由
  `output/global-plan-20261102-round11/sentinel_control.py` 反向对照验证过
  （`patched=True refused=True counted=1`），所以 0 行等于 0 次模型调用。
  网络写入只有 `PUT bundle`、`POST cards/review`×4、`POST plans/draft`、`PUT session`×2、
  `PUT prep-selection`，全部 200，无 `cards/draft`；控制台 error 0 条。

## 6 命令与结果（干净候选）

全部经 `output/r1/run_test.py` 包装（临时库），用维护工作区的虚拟环境解释器运行候选目录的脚本：

| 命令 | 结果 |
| --- | --- |
| `output/r1/run_test.py scripts/test_same_scope_identity.py` | PASS |
| `… scripts/test_cross_chapter_identity.py` | PASS |
| `… scripts/test_global_plan_split_recovery.py` | PASS |
| `… scripts/test_local_digest_split_recovery.py` | PASS |
| `… scripts/test_empty_reply_recovery.py` | PASS |
| `… scripts/test_shared_generation_interfaces.py` | PASS |
| `… scripts/test_reasoning_effort.py` | PASS |
| `… scripts/test_merge_budget.py` | PASS |
| `… scripts/test_artifact_semantic_prompt_contract.py` | PASS |
| `… scripts/test_chapter_planning_budget.py` | PASS |
| `… scripts/test_artifact_workflow.py` | PASS |
| `… scripts/test_fantasy_artifact_workflow.py` | PASS |
| `… scripts/test_handout_coverage.py` | PASS |
| `… scripts/test_handout_narrative_use.py` | PASS |
| `ruff check backend scripts` | All checks passed |
| `python -m compileall -q backend scripts` | 退出码 0 |
| `node --check frontend/workbench.js` | 通过 |
| `git diff --check` | 通过（仅 CRLF 提示） |

未批量运行全部 `test_*.py`（仓库内仍有会连接真实服务的历史脚本）。

## 7 没有证明的事

1. 真实生成只跑完 6 张计划卡中的 3 张（预算上限），**整章质量、卡数收敛、去重在真实模型上的
   效果都没有完整证据**；上一轮的 14 张卡样本仍是“全批准测试库”，不能当质量验收。
2. 艾丽卡/约拿两张人物卡在本批没有生成，`wants` 无来源条件的问题在真实输出上**未验证**。
3. 页面上的“生成/重试按钮 click→请求→状态转换”没有取得：该板已有 14 项产物，点击
   `#draft-artifacts` 不发请求（服务端也本会 409），也没有用 `fetch` 替代点击。需要一块空板
   Fake 副本才能补。
4. 真人备团时间（“一小时准备两小时游戏”）未测；本轮不涉及。
5. `environment` 卡型本轮没有纳入同范围候选（只加了地点/NPC/威胁），跨章节路径保持原样。
