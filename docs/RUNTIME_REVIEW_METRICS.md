# P0.3 运行记录与复盘

状态：**实现与自动化回归已就绪；真实 GM 试跑待执行。**

P0.3 只记录 GM 在工作台内为了运行场景所做的操作。它不修改种子事实、不会把运行记录混入 `ExampleBundle`，也不采集玩家隐私或未经同意的外部数据。

## 记录范围

`SessionState.log` 保持旧的 `move`、`note` 和 `transition` 日志可读，并新增以下结构化事件：

| 事件 | GM 操作 |
|---|---|
| `run_started` | 明确开始一份场景计划 |
| `lookup` / `lookup_missing` | 查找事实或卡片；记录没有找到的 GM 信息 |
| `source_page_opened` | 从事实来源打开一个原文页 |
| `clue_revealed` | 在当前场景揭示直接或隐藏线索 |
| `clock_advanced` / `clock_rewound` | 推进或回退时钟 |
| `scene_changed` / `beat_changed` | 切换场景或节拍 |
| `gm_move` / `manual_note` | 记录 GM 移动与 GM 内部备注 |
| `field_edited` | 改写事实、卡片、计划或按页新增事实的字段 |

每条事件可带当前计划、卡片、节拍、主体类型与一小组标量元数据。后端会拒绝指向不存在计划、卡片或节拍的存档，日志最多保留 200 条。

## 复盘产物

工作台运行区提供两个下载入口，也可直接请求：

```text
/api/domain/examples/<example>/session/review?format=markdown
/api/domain/examples/<example>/session/review?format=json
```

复盘回答 P0.3 的三项问题：

- 哪些 GM 信息没有找到；
- 哪些卡片被反复查找或停留；
- 哪些字段被人工改写。

JSON 另外包含事件计数、已打开来源页、已揭示线索、时钟与场景变化、当前运行状态和保存状态。Markdown 是面向 GM 的简洁摘要，不会从日志中推断玩家行为。

## 回归检查

```powershell
python -m compileall -q backend scripts
node --check frontend/workbench.js
python scripts/test_runtime_review.py
python scripts/test_runtime_review_api.py
```

`test_runtime_review.py` 覆盖结构化汇总、字段改写、来源页、信息缺口与旧日志兼容。`test_runtime_review_api.py` 对实际 FastAPI 路由验证 JSON/Markdown 内容类型与下载响应头。

## 仍待执行的验收

真实 GM 需要用 P0.2 的奈面单章包至少试跑一次，并在复盘中确认上述三项问题都能被回答。试跑前，P0.3 的正确状态是“可试跑、待验收”，不是“最终完成”；P1 的 LLM 影子模式仍不得提前接入。
