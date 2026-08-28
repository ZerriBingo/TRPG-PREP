# trpg-prep —— TRPG 备团助手

一个面向 GM 的离线优先备团工作台原型。它不把长模组一次性改写成另一篇长文，而是计划把 GM 选定的跨页章节范围整理成带来源的事实、目标板块备团产物和可运行场景。

当前首个真实试点是《奈亚拉托提普的面具》第二章“恐怖的咀咀屋”。用户界面按“现实恐怖 / 奇幻冒险 / 通用备团”三个目标板块组织；内部仍保留兼容的运行与整理 profile 契约。

**当前真实状态（2026-08-28）**：人工金标准卡片已得到“确实能用”的 GM 反馈，来源预览、手工编辑、场景运行和复盘可用；工作台以 PDF 上传和连续/离散跨页范围为入口。书架代表备团项目，重新分析会在同一项目内创建递增分析版本；机械窗口只声明固定页数、字符预算或范围末端等真实切分原因，标题/续写/句末仅作为非语义诊断信号。200+ 候选可由前端自动分批复核，旧客户端的大请求也不会再触发 100 条上限错误。R3 的大事实集流程已改为“局部整理 -> 全局规划 -> 回读原始事实落卡”，每个步骤和尝试均持久化，失败重试复用已成功步骤，全部校验通过后才原子写入。284 条奈面事实已在数据库副本中完成确定性分层验证；当前真实上游也已用 6 条事实在副本中生成 3 张卡。真实完整章节的产物质量仍需 GM 显式验收。当前维护记录见 `docs/MAINTENANCE_2026-08-28_HIERARCHICAL_ARTIFACTS.md`。

## 快速开始

Windows 下：

- 双击 `start.bat`：打开可见控制台并启动服务，适合查看日志。
- 双击 `start.vbs`：隐藏控制台启动服务，并自动打开浏览器。
- 双击 `stop.bat`：停止监听 `8000` 端口的服务。

`start.bat` 会检查 Python、FastAPI、Uvicorn 和 PyMuPDF 是否可用；缺少依赖时会显示安装命令。首次运行前也可以手动执行：

    python -m pip install -r backend/requirements.txt

启动命令应从项目根目录运行。然后访问 `http://127.0.0.1:8000`，根路径会直接进入 GM 工作台：

`http://127.0.0.1:8000/workbench.html?example=naimen_pilot`

## 领域工作台

当前已实现的人工链路是：

`SourceFact -> RuleProfile -> DerivedCard -> 场景计划 -> 运行模式`

目标 LLM 链路是：

`跨页备团范围 -> LLM 候选 -> GM 复核/提升 -> 备团产物草案 -> 运行场景 -> 运行模式`

这条链路已经具备最小工程闭环；R2 的聚类、拆分/合并和完整多来源编辑，以及 R3 的真实模型产物质量与完整单元包验收仍未完成。

- **事实**：短摘要、类型、可见性、来源 PDF 页码、关联和备注。
- **备团板块**：界面统一显示现实恐怖、奇幻冒险和通用备团；内部 profile 定义行动、风险、失败语义、GM 移动、桌边卡型或材料整理结构。
- **派生卡**：由事实组合而成，可按场景、NPC、威胁、时钟、环境或证据链组织。
- **场景计划**：沿用备团任务已确定的来源、跨页范围、目标板块和时长，自动使用该书架的已批准产物，离线编排为可编辑节拍；不再要求 GM 重复填写标题、文件、页码或场景前提。草案生成后先检查，明确开始后才进入运行状态。
- **编辑闭环**：可编辑/新建/删除事实和卡组，保存到 SQLite 覆盖层，也可以还原种子包。
- **原文辅助**：事实编辑器可打开来源 PDF 页，查看该页文本和图片；`fixture://` 等非真实 PDF 来源不会被伪装成原文。
- **GM/玩家边界**：软提示、硬推进、线索、NPC 动机、现场入口和节拍问题只显示给 GM；玩家听到的是 GM 根据当前虚构现场说出的描述，不是行动菜单。

当前可用样例：

- `naimen_pilot`：奈面“恐怖的咀咀屋”，20 条事实、7 张 Cthulhu Dark 卡。
- `red_signal_fixture`：离线综合夹具，不代表任何真实模组原文。

运行模式以已启动计划的当前场景、已揭示线索、推进钟、GM 移动和运行日志为核心；没有启动计划时只显示规则提示，运行操作会被禁用，不把种子卡误当成当前场景。运行状态独立保存，不改写领域种子包。复盘只汇总 GM 在工作台中的查找、来源页、线索、时钟、场景、手工备注与字段改写，不自动采集玩家隐私或外部数据。

当前新增工作流是：上传 PDF → 输入如 `159-165, 172` 的跨页范围 → 选择板块和固定时长 → 创建并开始 → 按窗口查看候选 → 连续复核并显式送入书架 → 从书架生成备团产物草案 → 编辑/批准产物 → 组装运行场景 → 明确开始运行。模型由“模型连接”中的当前配置统一决定。下游继承任务的 PDF、页范围、板块和时长，不再要求重复输入；模型草案不会自动批准，已被运行场景引用的产物也不能退回修改或删除。

导出接口：

    /api/domain/export?example=naimen_pilot
    /api/domain/examples/naimen_pilot/session/review?format=markdown
    /api/domain/examples/naimen_pilot/session/review?format=json


## 项目结构

    frontend/              原生 JavaScript 前端，无构建步骤
    backend/app/           FastAPI 路由、领域工作台、PDF 原文预览、SQLite 存储
    backend/domain/        规则无关模型、规则档案、样例运行包
    backend/skills/        保留的规则化 schema 资源
    scripts/               校验和 PDF 辅助脚本
    data/                  运行时数据库、上传文件和导出物
    Resource/              参考规则与模组，不自动上传或改写

## 设计边界

- 不把 660 页奈面全文复制进领域样例；事实保持短小，并保留来源页码。
- 原文事实、推断和 GM 建议分开标记；卡片不是原文的替代品，而是桌边调度层。
- Cthulhu Dark 优先生成调查压力、恐怖、线索揭示和不可逆后果，不强行制作敌人数据面板。
- Daggerheart 是奇幻冒险运行档案；通用模组整理档案服务于 D&D、COC 等旧模组的拆解和转换，不是第三种桌边规则。
- LLM 生成的内容仍必须经过人工审核；接受候选不会自动写入事实或运行包，只有单独、可审计的提升操作会把它复制为书架事实。提升也不会绕过 R3 自动生成或批准卡片。

## 校验

    python scripts/validate_domain.py --example red_signal_fixture
    python scripts/validate_domain.py --example naimen_pilot --write-markdown data/light_results/naimen-pilot-cards.md
    python -m compileall -q backend scripts
    node --check frontend/workbench.js
    python scripts/test_prep_job.py
    python scripts/test_scene_plan_context.py
    python scripts/test_artifact_workflow.py
    python scripts/test_shadow_mode.py
    python scripts/test_shadow_candidate_diff.py
    python scripts/test_shadow_review.py
    python scripts/test_evidence_status.py
    python scripts/test_runtime_review.py
    python scripts/test_runtime_review_api.py
    python scripts/test_maintenance_r4.py

当前有效的方向、三板块产物契约、LLM 复核方案和阶段路线见 [`docs/README.md`](docs/README.md)。2026-08-25 及更早的重评/重做文档是历史记录。
