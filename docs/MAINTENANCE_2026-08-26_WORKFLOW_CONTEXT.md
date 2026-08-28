# 2026-08-26 导航、板块命名与场景上下文维护记录

## 触发反馈

GM 在真实工作流中指出三处问题：

- 导航把“候选复核”放在接近末尾的位置，复核后又要切回前面的书架，顺序与实际流转相反。
- 书架“场景编排”仍沿用种子夹具时期的独立表单，要求再次选择档案、文件、页码、卡片并填写标题和场景前提。
- 用户已经在备团任务中确定来源 PDF、跨页范围、目标板块和本次时长；下游重复输入既增加劳动，也允许同一任务出现互相冲突的上下文。

## 工作流决定

顶层导航固定为：

`备团任务 -> 候选复核 -> 书架 -> 事实网 -> 卡组 -> 运行模式`

这表示产品流转顺序，不表示每个阶段已经实现。事实网和卡组仍可用于检查或兼容人工样例；真实任务在 R3 产物生成完成前不会因为页面存在就被视为可运行。

## 场景上下文所有权

场景包不再拥有第二套备团输入。任务工作区中的以下值由创建时的 `PrepScope` 持久化，并由服务端继承：

- 来源文件与版本；
- 一个或多个 `PageSpan`；
- 现实恐怖、奇幻冒险或通用备团目标；
- 本次游戏时长。

`POST /api/domain/examples/{example}/plans/draft` 不再接收标题、文件、页码、场景前提、板块或卡片选择。服务端从保存的任务和书架推导这些值；即使旧客户端仍发送旧 JSON 字段，也不会影响结果。

场景包使用以下确定性规则：

1. 任务工作区只使用任务已经固定的目标板块。
2. 自动纳入该板块下所有来源不含 `model_candidate` 的可编排产物。
3. 必须至少有一项已批准的场景、调查地点或环境产物。
4. 通用备团是材料整理板块，不能直接进入桌边运行。
5. 没有 R3 产物时入口禁用并说明“等待产物生成与复核”，不能要求 GM 用自由文本补齐。
6. 历史种子样例没有 `PrepScope`，仅为兼容验证从已确认卡片引用的事实中恢复唯一来源文件和精确页段。
7. 相同上下文和相同产物重复生成时返回既有草案，不堆积仅 ID 不同的重复计划。

当前 `ScenePlan` 仍保存单一 `source_file`。真实备团任务本身一次只绑定一个 PDF，因此不会重复选择文件；历史工作区若跨多个文件则拒绝静默合并，后续应由任务/单元边界分别生成。

## 用户可见命名

界面不再展示 `Cthulhu Dark 2E`、`Daggerheart 叙事档案` 或“规则档案”作为主要选择名称，统一使用：

- 现实恐怖；
- 奇幻冒险；
- 通用备团。

内部 `profile_id`、`RuleProfile` 与 JSON 文件名暂时保留，避免破坏领域校验、样例和运行状态。内部兼容名不是新的用户输入，也不表示继续扩展规则 profile。

## 正式数据状态

维护时正式“无光的灯塔 · p1-20”工作区有 46 条已提升事实、0 张派生卡、0 份场景计划。当前正确状态是：

- 书架可见，事实没有丢失；
- 场景包按钮禁用；
- 明确提示还缺已批准备团产物；
- 不自动生成、批准或提升任何正式内容。

这不是故障，而是 R2 已到达书架、R3 尚未实现的真实边界。

## 代码与验证

主要改动：

- `backend/app/prep.py`：按工作区查找所属备团任务。
- `backend/domain/service.py`：新增完全由工作区上下文驱动的场景计划组装入口。
- `backend/app/main.py`：工作台返回只读 `prep_context`；场景草案接口不再接受自由输入。
- `frontend/workbench.html/js/css`：导航重排、三板块命名、只读确认框、缺产物禁用状态和响应式修复。
- `scripts/test_scene_plan_context.py`：验证旧请求字段被忽略、任务范围被继承、缺产物时返回 422。

已通过：

- `python scripts/test_prep_job.py`
- `python scripts/test_scene_plan_context.py`
- `python scripts/test_shadow_mode.py`
- `python scripts/test_shadow_review.py`
- `python scripts/test_shadow_candidate_diff.py`
- `python scripts/test_evidence_status.py`
- `python scripts/test_runtime_review.py`
- `python scripts/test_runtime_review_api.py`
- `python scripts/validate_domain.py`
- `python -m compileall backend/app backend/domain`
- `node --check frontend/workbench.js`

浏览器验证：1280 x 900 与 390 x 844 均无页面级横向溢出；导航顺序正确；正式任务显示禁用原因；奈面样例确认框包含 0 个输入控件并自动列出 7 项产物；控制台无 warning/error。

## 下一步

继续当前 backlog，而不是重新扩展场景表单：

1. 完成 R2 多来源编辑、聚类、拆分/合并与冲突标记。
2. 实现 R3“批准事实 -> 可复核备团产物草案”。
3. 只有产物被明确批准后，当前场景包入口才自动解锁。
