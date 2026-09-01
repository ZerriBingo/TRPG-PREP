# 模型提供商接入调研（README 0.1.2d）

调研日期：2026-09-01。

本文件只为用户 README 的模型接入说明提供证据；外部资料仅使用提供商自己的文档或 API，仓库结论仅依据当前工作区源码。

## 可放入 README 的结论

- **推荐 OpenRouter 作为低频试用入口。** OpenRouter 的 Free Models Router 使用模型 ID `openrouter/free`，会从当前可用的免费模型中选择；官方将它定位为实验、学习和低使用量场景，并明确说明免费模型可能受较低限流、可用性变化和高峰期延迟影响。[OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router)
- **不要把 OpenCode Go 写成“免费模型”入口。** 正确名称是 **OpenCode Go**；官方写明它是每月 10 美元的订阅，使用量有 5 小时、每周和每月的额度上限。其文档虽称超过额度后可以继续使用“免费模型”，但没有在 Go 的模型列表中标出哪些模型免费，模型 API 也只返回 ID、没有价格或免费标记。因此 README 不应承诺某个 Go 模型免费，也不应承诺免费额度。[OpenCode Go：使用量与价格](https://opencode.ai/docs/go/)；[OpenCode Go Models API](https://opencode.ai/zen/go/v1/models)
- **当前版本不应把 OpenCode Go 当作已支持的推荐提供商。** OpenCode Go 的官方定位是 OpenCode 和产生相似请求的编码代理，并要求调用工具使用可识别、非泛化的 User-Agent；本项目固定发送通用 Chrome User-Agent。因此在修改请求头、确认服务条款与非编码工作负载适配前，README 最多只能把它列为“未验证的高级手动配置”，更稳妥的是暂不列入用户指南。[OpenCode Go：适用范围与流量要求](https://opencode.ai/docs/go/)；[当前请求头实现](../backend/app/llm.py#L22-L33)

## 本项目的实际配置契约

本项目的真实 LLM 配置不是环境变量教程。工作台的“模型连接”表单提交 `base_url`、`model`、可选 `api_key` 和 `fake`；后端把这些值保存在项目 `data/app.db` 的 SQLite `config` 表中。README 不应要求用户设置 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 或任何未被当前实现读取的环境变量。[模型连接表单](../frontend/workbench.html#L67-L76)；[前端保存逻辑](../frontend/workbench.js#L502-L538)；[SQLite 配置读写](../backend/app/storage.py#L15-L22)；[配置默认值与持久化](../backend/app/storage.py#L1010-L1043)

| 字段 | 当前行为 | README 应怎么写 |
| --- | --- | --- |
| `Base URL` | 若 URL 以 `/v1` 结尾，客户端请求 `${base_url}/chat/completions` 和 `${base_url}/models`；若不含版本路径，则自动补 `/v1`；若填完整的 `/chat/completions`，则聊天请求直接使用该 URL。[URL 拼接规则](../backend/app/llm.py#L50-L87) | 填提供商给出的 **API base URL**，不要填网页地址。 |
| `API Key` | 请求使用 `Authorization: Bearer <api_key>`；保存后接口只回传是否已保存，但实际值写入本地 SQLite 配置表。[请求头实现](../backend/app/llm.py#L22-L33)；[配置接口](../backend/app/main.py#L468-L493)；[SQLite 配置读写](../backend/app/storage.py#L1010-L1043) | 在工作台输入 Key；不要把 Key 写进 README、命令行示例或仓库文件。 |
| `模型` | 真实调用总是 OpenAI Chat Completions 形式；“测试连接”会保存配置后请求 `${base_url}/models`，只证明模型列表端点可用，并不发送一次聊天完成请求。[聊天与模型列表实现](../backend/app/llm.py#L50-L87)；[测试连接流程](../frontend/workbench.js#L540-L578) | 填提供商当前模型 API 返回的精确 `id`；把“测试连接”描述为模型列表测试，而非完整生成验收。 |
| `使用离线 FakeLLM` | 该开关为真时，后端使用 FakeLLM；实际备团任务在“非 FakeLLM 且无 API Key”时会拒绝启动。[客户端选择逻辑](../backend/app/llm.py#L662-L669)；[任务启动校验](../backend/app/main.py#L1036-L1045) | 使用真实模型时取消勾选；没有 Key 时可以保留它进行离线流程试用。 |

## OpenRouter

### 官方接入事实

1. 在 OpenRouter 创建一个命名 API key；官方允许为 key 设置可选的信用额度上限，并要求 API 调用使用 Bearer token。[OpenRouter API Authentication](https://openrouter.ai/docs/api_reference/authentication)
2. 本项目应填写的 Base URL 是 `https://openrouter.ai/api/v1`。这是 OpenRouter 给 OpenAI SDK 的 `base_url`，与本项目的 `/v1/chat/completions` URL 规则完全匹配。[OpenRouter API Authentication](https://openrouter.ai/docs/api_reference/authentication)；[本项目 URL 拼接规则](../backend/app/llm.py#L50-L87)
3. 低风险的免费试用模型可以填写 `openrouter/free`。它会随机选择满足请求能力要求的免费模型，响应会报告实际使用的模型；路由器本身和被路由的免费模型均不收费。[OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router)
4. 若用户希望锁定某个免费模型，应先查看实时模型目录/API，并只使用目录中实际提供免费变体的精确 `:free` ID；不要根据展示名称、旧教程或自行给任何 ID 追加后缀来猜测。OpenRouter 说明免费模型可用性经常变化，且其 `GET /api/v1/models` 返回每个模型的 `id` 与 `pricing`，当前免费条目将文本 prompt/completion 价格标为 `"0"`。[OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router)；[OpenRouter Models API（实时）](https://openrouter.ai/api/v1/models)

### 推荐的 README 说明（可直接改写）

> 可先使用 OpenRouter 做低频试用：在 OpenRouter 创建 API Key 后，在“模型连接”中填入 Base URL `https://openrouter.ai/api/v1`、API Key，并取消勾选“使用离线 FakeLLM”。模型可先填 `openrouter/free`；它会自动选择当前可用的免费模型。免费模型适合试用，可能遇到限流、暂时不可用或较高延迟；需要固定模型时，请以 OpenRouter 当前模型列表/API 中的精确 ID 为准。

以上配置值与限制均来自 [OpenRouter Authentication](https://openrouter.ai/docs/api_reference/authentication) 和 [OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router)，并符合 [本项目模型连接 UI](../frontend/workbench.html#L67-L76) 与 [OpenAI 兼容客户端](../backend/app/llm.py#L50-L87)。

## OpenCode Go

### 产品名称、技术兼容性与接入路径

1. 用户所说的产品的官方名称是 **OpenCode Go**。官方流程是：登录 OpenCode Zen、订阅 Go、复制 API Key；文档中的 `/connect` 是给 OpenCode TUI 使用的步骤，不是本项目需要执行的命令。[OpenCode Go：How it works](https://opencode.ai/docs/go/)
2. OpenCode Go 确实提供 API endpoint。官方 Endpoint 表把部分模型列为 `https://opencode.ai/zen/go/v1/chat/completions`，并标注为 `@ai-sdk/openai-compatible`；完整模型 ID 与元数据可从 `https://opencode.ai/zen/go/v1/models` 获取。[OpenCode Go：Endpoints 与 Models](https://opencode.ai/docs/go/)；[OpenCode Go Models API](https://opencode.ai/zen/go/v1/models)
3. 若未来修正下面的兼容性风险，技术上可尝试的表单值是：Base URL `https://opencode.ai/zen/go/v1`、API Key 为 Go 订阅后复制的 key、模型为当前官方 Endpoint 表中标注 `chat/completions` 的精确 ID，且取消勾选 FakeLLM。项目会把该 Base URL 分别扩展为 `/chat/completions` 和 `/models`，两者都有 OpenCode Go 官方资料支持。[OpenCode Go：How it works、Endpoints 与 Models](https://opencode.ai/docs/go/)；[本项目 URL 拼接规则](../backend/app/llm.py#L50-L87)；[模型连接 UI](../frontend/workbench.html#L67-L76)
4. 不要把 `/v1/models` 返回的每个 Go 模型都当作可用候选：OpenCode Go 还列出使用 `/responses` 或 `/messages` 的模型，而本项目只会调用 Chat Completions。因此仅可选择官方 Endpoint 表当前标成 `chat/completions` / `openai-compatible` 的模型。[OpenCode Go：Endpoints](https://opencode.ai/docs/go/)；[本项目聊天调用](../backend/app/llm.py#L50-L87)

### 不建议直接放进 README 的原因

- OpenCode Go 文档的“Where can I use it?”将服务定位为 OpenCode 和具有相似请求类型的编码代理；trpg-prep 是备团工作台，官方没有声明支持该工作负载。[OpenCode Go：适用范围](https://opencode.ai/docs/go/)
- OpenCode Go 要求工具正确标识自己且不使用 broad User-Agent，而项目当前发送的是通用浏览器 UA。以当前代码引导用户接入，可能不满足该提供商公开的使用条件。[OpenCode Go：流量要求](https://opencode.ai/docs/go/)；[当前 User-Agent](../backend/app/llm.py#L22-L33)
- OpenCode Go 当前是付费订阅而非免费 API；其文档确实提及额度耗尽后可以使用“免费模型”，但没有为 Go 给出可验证的免费模型 ID、价格字段或免费额度。因此“免费模型”“免费额度”“永远可免费使用”都不应出现在 README 的 Go 指引中。[OpenCode Go：使用量、额度与价格](https://opencode.ai/docs/go/)；[OpenCode Go Models API](https://opencode.ai/zen/go/v1/models)

## README 的措辞边界

- 可以说“OpenRouter 的 `openrouter/free` 适合低频试用”，但必须同时写明模型会动态选择，且可能受限流、可用性和延迟影响。[OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router)
- 不要写“OpenRouter 免费模型稳定”“任意 `:free` 都存在”或在 README 固定某个免费模型名称；应让用户以实时模型目录/API 为准。[OpenRouter Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router)；[OpenRouter Models API（实时）](https://openrouter.ai/api/v1/models)
- 不要写“OpenCode Go 免费”或“OpenCode Go 已支持 trpg-prep”。前者与订阅定价不符，后者超出官方的编码代理适用范围，且当前 User-Agent 与其公开要求冲突。[OpenCode Go](https://opencode.ai/docs/go/)；[当前请求头实现](../backend/app/llm.py#L22-L33)
- 不要把模型配置写成环境变量步骤，也不要承诺 Key 使用系统密钥库或加密保存；当前实现是工作台表单加 SQLite 配置表。[模型连接表单](../frontend/workbench.html#L67-L76)；[SQLite 配置读写](../backend/app/storage.py#L1010-L1043)
