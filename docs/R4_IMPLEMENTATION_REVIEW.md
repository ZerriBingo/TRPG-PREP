# R4 独立复审与签收（2026-10-31）

状态：R4 已签收并选择性同步主目录，保持未提交。规范轴无阻断；需求轴首次一项 P2 由父代理直接修正后，两轴均无剩余阻断。下一轮见 R6_IMPLEMENTATION_HANDOFF.md。不声明真实模型产物质量或备团时间目标已通过。

## 基线与范围

基线是 R3/R5 签收后的主目录未提交工作区；候选为 .claude/worktrees/cranky-mendeleev-676227。双方 HEAD 同为 5c0f239e6c98843d76eb153316c7a96fd4b56930，无新增提交；按两边文件增量审查，不把历史 dirty 内容归入本轮。

首次增量十文件：artifacts.py、llm.py、两份 profile、workbench.js、三个脚本及 audit/backlog。修改前摘要、快照及差异在忽略的 output/r4-review-20261031-190659/。R4_IMPLEMENTATION_HANDOFF.md、PRODUCT_GUARDRAILS.md 为需求依据，实施方 audit 为待验证声明。

## 规范轴

首次独立规范审查无阻断；父代理收尾后的增量复核亦无阻断。

- 四个产物消息构造器只发送板块结构与所需卡型契约；剩余两处完整 profile 序列化用于持久步骤哈希，不进入模型消息。
- FakeLLM 消费 BOARD_PROFILE_JSON；真实客户端仍接收普通消息列表。通用校验纠正和 JSON 格式重试保留原消息；coverage、identity、规划索引请求没有另行加载完整档案。
- 保留兼容 ID、卡型、来源、审批及准备/运行状态；未新增规则转换引擎或第二套配置模型。
- 修正一处非阻断测试笔误：摘要循环此前检查循环外 daggerheart 的 profile_kind，现检查当前档案，module-prep 预期为 prep。

## 需求轴

首次一项 P2，现已关闭：新增共享边界要求“keep supplied numbers only … in their source fields”，不加区分地覆盖所有数字。现实恐怖卡没有 source_statistics/source_statblock；局部消化、规划结构也无此类字段。“仓库有6名看守，每10分钟换岗”等事实与正文仅许声明字段的要求冲突，违反不能为了中性化丢失可运行信息的边界。

父代理直接将限制收窄为规则统计与机制：有专用来源字段时保留原文；无此字段时通过引用事实保持规则文本可查，不补造字段。同时明确有来源的人数、日期、时刻与距离可进入适合的叙事字段。最终提示版本 draft-v13/global-plan-v18，避免复用实施方 v12/v17 的过宽指令缓存。两轴增量复核均确认关闭。

其余需求核对：

- 局部消化、整板规划、逐卡正文及兼容直接草稿共享边界，去除规则名、规则档案摘要、risk_axes、failure_moves、gm_moves、prompts、scene_guidance 等元信息，保留本阶段需要的卡型结构。不是只改界面名。
- 抽取/合并仅携带兼容 ID 和中性目标；章节/剧情线规划未发送规则档案，保留有代码依据。通用板块未成为必经转换阶段。
- 原有叙事必填不要求参考系统 Tier/Difficulty/Type；有来源事实、字段引用的 DND source_statblock 可通过校验并原样保留。缺叙事必填仍拒绝，未降低审批资格。
- 运行面板标注方法参考不替代实际规则。界面只新增一段文案，未增加操作或改变运行状态。

## 独立验证

22 项 Python 通过：rule_reference_boundary、artifact_semantic_prompt_contract、prep_prompt_contract、fantasy_prompt_contract、artifact_workflow、fantasy_artifact_workflow、fantasy_long_artifact_workflow、artifact_job_state、artifact_question_summary、artifact_shape_normalization、plan_coverage_recovery、chapter_planning_budget、json_format_retry、state_anchor_contract、prep_selection、story_structure_contract、runtime_layout_contract、location_runtime_contract、runtime_material_projection、new_project_contract、scope_location_contracts、scene_plan_context。

5 项 Node 通过：prep_selection_view、runtime_retrieval、runtime_story_beats、thread_development_view、source_detail_decoupling；另通过 JS 语法、改动 Python 的 ruff 与 git diff --check。同步后在主目录重跑上述检查。

父代理扩充 rule_reference_boundary 的实际行为验证：

- 两板块各走四个真实构造器与 _run_json_step；先返回无效对象触发实际 validator，再用离线 FakeLLM 返回有效结果，检查首次/纠正消息边界和来源、原消息保留、成功步骤复用不再调用客户端。
- 现实恐怖 normal_state/arrival_description 保留“6名看守／10分钟”且有字段来源；原 COC 文本仍在引用事实。DND 数值样例也补 SourceFact/SourceRef，不再用无事实的卡片充当来源证据。
- 实际 LLMClient.chat_json 以拦截 chat 返回无效 JSON→有效 JSON 验证格式重试，无网络或 worker；第二次请求保留原消息。
- 两个反向检查均失败于正确原因：恢复完整 profile 被检测到规则元信息；恢复所有数字限制被检测到压制叙事数量。仅临时替换进程内函数/常量，未修改候选源码或业务数据。

## 同步与限制

只复制已审查的十文件增量及签收/交接/索引文档；其他历史未提交文件逐字节校验，未回退、清理或整树覆盖。同步清单与前后摘要在同一 output 目录。正式 data/app.db SHA-256 仍为 1c71e75f49c847852b44187af13acb6f9214699d959e6094579dfc47aeeea575；未提交、推送或发布。

全部写入在临时合成库，并用外层临时路径保护旧脚本 finally 的 init_db。未读取业务正文或调用真实模型。只签收消息契约、程序路径及确定性数据保留；FakeLLM 不证明模型一定服从指令，引用存在也不证明语义支持正确。界面仅增说明，未另做真实浏览器走查，由布局契约及既有函数回归检查。长篇忠实度、实际带团与时间目标继续待实测。

为 R6 另作一次合成 HTTP 检查：旧 scene 卡型项目在列表 validation_error=null，打开却 422；有效项目同时可打开。仅保存证据，本轮未修改 R6 代码。
