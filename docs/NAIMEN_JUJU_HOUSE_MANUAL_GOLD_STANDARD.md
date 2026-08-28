# 奈面“恐怖的咀咀屋”人工金标准

状态：**运行包与自动校验已就绪；真实 GM 试跑待执行。**

本文件记录 P0.2 的人工基线。它只覆盖《奈亚拉托提普的面具》PDF p159-165 的“恐怖的咀咀屋”，目的不是重写该章或扩展战役，而是验证一份可追溯的现实恐怖单元包是否足以支持一次桌边运行。

## 交付物

- data/fixtures/naimen_juju_house_unit.json
  - 独立的人工运行单元，不塞入当前 ExampleBundle 或运行档案。
  - 完整保留对 naimen_pilot 的 20 条事实与 7 张已批准卡的引用，原种子包不被覆盖。
- scripts/validate_naimen_juju_house_unit.py
  - 先加载并校验 backend/domain/examples/naimen_pilot.json，再校验单元包。
  - 拒绝模型候选、失效的事实或卡引用、越出 p159-165 的本地来源、没有前推方式的核心线索、没有可见阶段变化的时钟，以及少于三种收束的包。
- data/light_results/naimen-juju-house-unit.md
  - 校验器导出的 GM-only 快速运行视图。

## 内容边界

单元包包含：

- 谜团简报和 GM-only 背景。
- 四个场景、四条开放调查方向和八条线索。
- 恩科万、姆达里、罗伯森、普尔四个功能 NPC。
- 西姆巴守卫、查寇塔、教派/腐败警力反应网三个威胁。
- 月朔仪式钟与暴露/清算钟；每一阶段都描述可见变化和后果。
- 普尔突袭、罗伯森泄密、直接干预三种收束。
- 未回收线索和通向警方选择、姆达里路线、壁龛物品与更大战役的下一站入口。

它不做以下事情：

- 不修改 backend/domain/examples/naimen_pilot.json。
- 不修改 backend/domain/profiles/cthulhu-dark-2e.json，也不抢跑 P2.1 的正式卡型扩展。
- 不接入 LLM、不生成候选、不做批量处理。
- 不扩展旧 overview、locations、encounters 管线。
- 不把 GM 编排、推断或跨章假设伪装成原文事实。

## 来源规则

夹具里的每个叙事对象都使用同一种来源包装：

~~~json
{
  "text": "供 GM 使用的简短内容",
  "evidence_status": "source_fact | inference | gm_authored",
  "visibility": "gm_only | mixed | player_safe | handout",
  "fact_ids": ["fact_naimen_..."],
  "source_refs": []
}
~~~

- source_fact 与 inference 必须关联已确认的试点事实或 p159-165 的真实 PDF 页引用。
- gm_authored 是主持人的结构、失败前推、现场推进和开放选择建议；它明确显示为 GM 编排。
- model_candidate 在这份人工基线中被硬性禁止。

标题、稳定 ID 与列表关系是导航元数据，不是无来源叙事断言。

## 使用与验证

重新验证并生成运行视图：

~~~powershell
python scripts/validate_naimen_juju_house_unit.py --write-markdown data/light_results/naimen-juju-house-unit.md
~~~

GM 运行时只需打开导出的 Markdown；原 PDF 只在需要核对来源细节时回看。推荐从“开场”开始，根据调查方向切入场景，按现场信号推进两只钟，并在“线索网”中用失败前推保持信息流动。

## 试跑验收

P0.2 还不能宣告最终验收，因为规划要求至少一次真实 GM 试跑。试跑应确认：

1. 不打开整本 PDF，也能说清开场、至少两条调查方向、两只时钟和三种收束。
2. 每个核心线索在失败、错过或代价出现后仍有继续路径。
3. 现场能快速找到 NPC 功能、威胁反应、来源页和下一站入口。
4. 记录翻页、遗漏、重复与玩家偏航；这些记录属于 P0.3 的运行指标，不回写为未经确认的原文事实。

试跑结束前，本产物的正确状态始终是“已就绪，待试跑”，而不是“P0.2 完成”。
