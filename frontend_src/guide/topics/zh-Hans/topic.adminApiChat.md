---
{ "id": "topic.adminApiChat", "title": "Agent Chat 端点", "parent": "topic.adminApi", "order": 40, "keywords": ["提供商", "AI 提供商", "账户", "模型", "聊天", "智能体轮次", "SSE", "流"], "sectionKeywords": { "providers": ["提供商账户", "实例", "instance_id"], "provider-kinds": ["Anthropic", "OpenAI", "Claude", "ChatGPT", "Gemini", "Ollama", "OpenRouter", "Infron", "Qwen", "TypeSafe", "JEV", "决策提供商", "endpoint_id", "区域"], "setup-fields": ["api_key", "base_url", "endpoint_id", "API 密钥"], "list-providers": ["provider_types", "purpose", "capabilities"], "change-provider": ["自动模型", "auto", "model_mode"], "refresh-models": ["刷新", "能力"], "probe-model": ["探测", "选项", "努力", "视觉"], "check-setup": ["探测", "测试连接"], "chat": ["agentcli/chat", "轮次", "access_key_id"], "chat-options": ["思考", "努力", "温度", "max_tokens", "show_thinking"], "chat-images": ["附件", "图像", "JPEG", "PNG", "GIF", "base64"], "events": ["保活", "text/event-stream", "Server-Sent Events", "ready", "status", "thinking_delta", "assistant_delta", "focus_declined", "tool_call", "tool_progress", "approval_required", "approval_resolved", "tool_image", "tool_result", "usage", "notice", "continue_required", "error", "messages", "done", "事件流", "SSE"] } }
---
使用这些端点管理 Phoenix 使用的 AI 提供商账户，并从你自己的代码运行 Agent Chat 轮次。每个端点都遵循[管理 API](guide:topic.adminApi)概述中的规则。

相关：[{{label:agentchat.title}}](guide:window.agentChat)、[AI 提供商设置](guide:window.settingsProviders)、[错误](guide:topic.adminApi#errors)

## 提供商账户 {#providers}

```
GET    /api/phoenix-mcp/admin/agentcli/providers
POST   /api/phoenix-mcp/admin/agentcli/providers
PATCH  /api/phoenix-mcp/admin/agentcli/providers/{instance_id}
DELETE /api/phoenix-mcp/admin/agentcli/providers/{instance_id}
GET    /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models
POST   /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh
POST   /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe
POST   /api/phoenix-mcp/admin/agentcli/probe
```

添加、检查和移除 Phoenix 用来访问 AI 提供商的账户。对话账户服务 Agent Chat、语音智能体和 AI 任务。决策账户服务决策提供商。

### 提供商类型 {#provider-kinds}

每个账户都有一个 `kind`。带端点的类型接受 `endpoint_id`；列出的第一个值是默认值。

| 类型 | 用途 | 设置字段 |
|---|---|---|
| `claude`（Anthropic） | 对话 | `api_key` |
| `deepseek` | 对话 | `api_key` |
| `chatgpt`（OpenAI） | 对话 | `api_key`、`endpoint_id`：`global`、`us`、`eu`、`ae`、`au`、`ca`、`jp`、`in`、`sg`、`kr`、`gb` |
| `gemini` | 对话 | `api_key` |
| `grok` | 对话 | `api_key`、`endpoint_id`：`global`、`us` |
| `groq` | 对话 | `api_key` |
| `kimi` | 对话 | `api_key`、`endpoint_id`：`global`、`china` |
| `meta` | 对话 | `api_key` |
| `minimax` | 对话 | `api_key`、`endpoint_id`：`global`、`china` |
| `mistral` | 对话 | `api_key`、`endpoint_id`：`global`、`eu`、`us` |
| `nvidia` | 对话 | `api_key` |
| `mimo`（Xiaomi MiMo） | 对话 | `api_key`、`endpoint_id`：`payg`、`token_plan`、`token_plan_sgp`、`token_plan_ams` |
| `tencent`（Tencent Cloud TokenHub） | 对话 | `api_key`、`endpoint_id`：`singapore`、`guangzhou`、`silicon_valley` |
| `opencode` | 对话 | `api_key`、`endpoint_id`：`zen`、`console` |
| `ollama_cloud` | 对话 | `api_key` |
| `ollama`（本地） | 对话 | `base_url` |
| `openrouter` | 对话 | `api_key` |
| `infron` | 对话 | `api_key` |
| `together` | 对话 | `api_key` |
| `cerebras` | 对话 | `api_key` |
| `fireworks` | 对话 | `api_key` |
| `qwen` | 对话 | `api_key`、`base_url` |
| `zai` | 对话 | `api_key`、`endpoint_id`：`standard`、`coding`、`china` |
| `typesafe`（TypeSafe） | 决策 | `api_key`、`model` |

### 设置字段 {#setup-fields}

添加账户和检查设置接受相同的正文。

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | string | 必填。上面的类型之一。 |
| `api_key` | string | 除 `ollama` 外每种类型都必填。最多 4096 个字符，不能包含控制字符。Phoenix 绝不返回它。 |
| `base_url` | string | `ollama` 和 `qwen` 必填。只有 `ollama` 可以使用私有地址。其他每种类型都使用固定端点，并忽略此字段。 |
| `endpoint_id` | string | 仅用于带端点的类型。省略它表示默认值。 |
| `model` | string | 对话类型可选；留空表示自动跟随提供商。`typesafe` 必填：最多 200 个字符，且必须在提供商当前列表中。 |

### 列出账户 {#list-providers}

`GET /api/phoenix-mcp/admin/agentcli/providers`

| 字段 | 类型 | 说明 |
|---|---|---|
| `purpose` | query string | `conversation`（默认）或 `decision`。其他任何值返回空列表，而不是错误。 |

响应包含 `instances`（账户）和 `provider_types`（设置表单）。两者都按标签排序。

`provider_types` 中的每个条目有 `kind`、`label`、`label_key` 和 `fields`。每个字段有 `id`、`type`（`secret`、`url` 或 `choice`）、`label_key` 和 `required`。它还可能包含 `placeholder` 和 `choices`，每个选择有 `value`、`label` 和 `label_key`。

`instances` 中的每个账户有这些字段。API 密钥绝不在其中。

| 字段 | 说明 |
|---|---|
| `id` | 账户的 ID，用作 `{instance_id}`。 |
| `kind` | 提供商类型。 |
| `name` | 由 Phoenix 生成。仅当一种类型有多个账户时，它才会添加区分部分。 |
| `model` | 请求现在使用的模型。 |
| `model_mode` | `fixed` 或 `auto`。决策账户始终为 `fixed`。 |
| `model_replaced_from` | 提供商已停用的所选模型，或 `null`。 |
| `models_checked_at` | Phoenix 上次读取模型列表的时间。 |
| `base_url` | 按设置时的值。 |
| `endpoint_id` | 按设置时的值。 |
| `capabilities` | 按模型：提供商声明的、探测发现的，以及提供商在过去 30 天拒绝的选项。 |
| `capabilities_checked_at` | Phoenix 上次检查能力的时间。 |

### 添加账户 {#add-provider}

`POST /api/phoenix-mcp/admin/agentcli/providers` 接受[设置字段](guide:#setup-fields)。

对于对话类型，Phoenix 会运行提供商的凭据检查。对于决策类型，它会加载模型列表并要求你的模型在其中。如果通过，Phoenix 会存储账户，并尝试读取其模型列表和声明的能力。

响应为 `201`，包含 `{"instance": {...}}`，保存上面的账户字段。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.providerUnknown` | `kind` 不是已知类型。 |
| 400 | `invalid_request` | `adminError.providerApiKeyRequired` | 缺少 `api_key`。 |
| 400 | `invalid_request` | `adminError.providerEndpointInvalid` | `endpoint_id` 不是该类型的值之一。 |
| 400 | `invalid_request` | `adminError.ollamaUrlRequired`、`adminError.providerBaseUrlRequired` | 缺少必需的 `base_url`。 |
| 400 | `invalid_request` | `adminError.providerUrlInvalid`、`adminError.providerUrlScheme`、`adminError.providerRemoteUrlScheme`、`adminError.providerUrlHostRequired`、`adminError.providerUrlCredentialsForbidden`、`adminError.providerUrlFragmentForbidden`、`adminError.providerUrlAddressForbidden`、`adminError.providerUrlResolutionForbidden`、`adminError.providerRemoteUrlAddressForbidden`、`adminError.providerRemoteUrlResolutionForbidden`、`adminError.providerRemoteUrlResolutionFailed` | `base_url` 不是可接受的 URL。 |
| 400 | `invalid_request` | `adminError.providerConnectionFailed`，或提供商自身失败的键 | 凭据检查失败。`adminError.providerConnectionFailed` 会带有 `detail`。 |
| 400 | `invalid_request` | `adminError.providerModelRequired` | 决策类型没有 `model`。 |
| 400 | `invalid_request` | `adminError.providerModelNotListed` | 模型不在提供商的列表中。 |
| 400 | `invalid_request` | `adminError.decisionProviderRateLimited`、`adminError.decisionProviderTimeout`、`adminError.decisionProviderUnreachable`、`adminError.decisionProviderUnreadable`、`adminError.providerKeyRejected`、`adminError.decisionProviderCheckFailed` | 决策提供商检查失败：速率受限、超时、无法访问、无法读取、密钥被拒绝或其他失败。 |
| 409 | `already_exists` | `adminError.providerAlreadyConfigured` | 已有相同设置的账户存在。 |

### 更改模型 {#change-provider}

`PATCH /api/phoenix-mcp/admin/agentcli/providers/{instance_id}`

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string | 要使用的模型。对话账户接受任何模型 ID。决策账户需要提供商当前列出的模型。 |
| `auto` | boolean | `true` 会自动跟随提供商的模型。决策账户拒绝此项。 |

发送 `model` 或 `auto: true`，不要同时发送，也不要发送其他字段。使用 `auto: true` 时，Phoenix 会立即读取模型列表，因此响应会显示它选择的模型。

响应为 `200`，仅包含 `{"instance": {"id": ..., "model": ..., "model_mode": ...}}`，而不是完整账户。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.unknownFields` | 正文包含 `model` 和 `auto` 以外的字段。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | `auto` 不是布尔值。 |
| 400 | `invalid_request` | `adminError.providerModelOrAuto` | `model` 和 `auto: true` 同时发送。 |
| 400 | `invalid_request` | `adminError.providerModelRequired` | 两者都没有发送。 |
| 400 | `invalid_request` | `adminError.decisionModelRequired` | `auto: true` 发给了决策账户，而决策账户需要具体模型。 |
| 400 | `invalid_request` | `adminError.providerModelNotListed` 以及[添加账户](guide:#add-provider)下的决策提供商错误 | 决策账户的模型未列出，或检查失败。 |
| 409 | `conflict` | `adminError.providerAccountChanged` | 决策账户在检查期间发生了变化。请重新读取并重试。 |
| 404 | `not_found` | `adminError.providerAccountNotFound` | 没有账户具有此 ID。 |

### 移除账户 {#remove-provider}

`DELETE /api/phoenix-mcp/admin/agentcli/providers/{instance_id}` 不接受正文。它会移除账户并清除所有指向它的设置：

- 语音智能体的提供商和模型。
- AI 任务的提供商和模型，以及其实体和 Phoenix Assist 管道。
- 对于正在使用的决策账户：`decision_provider_id`、`decision_shadow_enabled`、全部三种工具审核模式，以及任何 `hybrid` 路由模式（会变为 `off`）。

如果你移除默认决策账户，另一个决策账户会成为默认。响应为 `200`，包含 `{"deleted": "<instance_id>"}`，即使 ID 不存在也是如此。

相关：[设置与系统端点](guide:topic.adminApiSettings#settings)

### 列出模型 {#list-models}

`GET /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models` 向提供商请求一次其模型。响应为 `{"models": [...]}`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured` | 没有账户具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerConnectionFailedNoDetail` | 无法访问决策提供商。 |

### 刷新模型和能力 {#refresh-models}

`POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh` 不接受正文。它会重新读取模型列表和声明的能力并存储它们。选项检查得出的努力级别和温度限制会继续保留，除非提供商现在声明了它们。它不发送补全请求，因此不消耗 token。对于 `ollama`，它会为每个模型发送一个请求。

响应包含 `models`、`capabilities`、`declared`（提供商是否报告能力）和 `checked_at`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured`、`adminError.providerAccountNotFound` | 没有账户具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerConnectionFailedNoDetail` | 无法访问决策提供商。 |

### 检查模型选项 {#probe-model}

`POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe` 不接受正文。它会询问提供商所选模型接受哪些选项。这会消耗额度。

Phoenix 最多发送 10 个非常简短的补全，并在图像支持未知时额外发送一次图像检查。结果会覆盖声明的能力。

响应包含 `model`、`probed`、`calls`、`checked_at`、`effort_checkable`、`answered` 和 `vision_checked`。当提供商拒绝了每个问题时，`answered` 为 `false`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured`、`adminError.providerAccountNotFound` | 没有账户具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerChooseModel` | 账户没有选择模型。 |

### 检查设置 {#check-setup}

`POST /api/phoenix-mcp/admin/agentcli/probe` 接受[设置字段](guide:#setup-fields)，检查它们并列出模型。它不存储任何内容。

设置或连接失败仍会返回 `200`，失败内容在正文中：

```json
{ "ok": false, "error": "English description.", "message_key": "adminError.providerConnectionFailed", "message_params": {}, "models": [] }
```

成功时正文为 `{"ok": true, "models": [...]}`。当文本来自提供商时，失败会携带 `message_passthrough: true` 而不是 `message_key`。`message_params` 仅在消息有值时出现。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.providerUnknown` | `kind` 不是已知类型。 |
| 409 | `already_exists` | `adminError.providerAlreadyConfigured` | 已有相同设置的账户存在。 |

## 运行聊天轮次 {#chat}

```
POST   /api/phoenix-mcp/agentcli/chat
```

运行一个 Agent Chat 轮次，并以 Server-Sent Events 流式返回。注意路径：它位于 `/admin` 之外，但仍需要管理员。

该路由遵循紧急停止开关。开关开启时，它返回 `503`。如果 Phoenix 启动时开关已开启，Home Assistant 会在你关闭开关前返回 `404`。

相关：[紧急停止开关](guide:topic.killSwitch)

### 请求体 {#chat-body}

```json
{
  "access_key_id": "...",
  "instance_id": "...",
  "user": "turn off the kitchen lights",
  "messages": [{"role": "user", "content": "..."}],
  "options": {"effort": "high"},
  "language": "en"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `access_key_id` | string | 必填。智能体工作所用的访问密钥。它必须处于活动状态。 |
| `instance_id` | string | 必填。要使用的提供商账户。 |
| `model` | string | 可选。覆盖此轮次中账户的模型。 |
| `user` | string | 新消息。除非发送 `attachments` 或 `continue: true`，否则必填。 |
| `attachments` | list | 此消息的图像。参见[图像](guide:#chat-images)。 |
| `messages` | list | 之前的轮次。Phoenix 不保留转录：从 `messages` 事件中保存列表，并在下一轮次中发回。 |
| `options` | object | 模型选项。参见[选项](guide:#chat-options)。 |
| `continue` | boolean | `true` 会恢复在轮次限制处暂停的轮次，不添加新消息。 |
| `language` | string | 命令路由使用的语言。面板始终发送它。 |
| `log_conversation_id` | string | 在对话日志中把轮次归入一个线程。 |

轮次限制是 `agentcli_max_iterations` 设置。回复风格、详细级别和家庭专注模式在请求开始时来自 Agent Chat 设置。

相关：[Agent Chat 设置](guide:window.settingsChat)、[命令如何路由](guide:topic.commandRouting)

### 选项 {#chat-options}

Phoenix 只在提供商支持的地方使用每个选项。

| 字段 | 类型 | 说明 |
|---|---|---|
| `thinking` | boolean | 开启模型的推理。默认 `true`。 |
| `effort` | string | `low`、`medium`、`high`、`xhigh` 或 `max`。默认 `high`。 |
| `show_thinking` | boolean | 是否显示模型的推理。 |
| `temperature` | number | 提供商的采样温度。 |
| `max_tokens` | integer | 模型最多可以写入的 token 数。默认 32000。 |

### 图像 {#chat-images}

每个附件为 `{"mime_type": "image/jpeg", "data": "<base64>"}`。

- 最多发送 4 张图像，每张为 JPEG、PNG 或 GIF。
- 每张图像解码后最多 2 MiB，每边最多 8000 像素，所有图像合计最多 4 MiB。
- 图像自身的头部必须与其 `mime_type` 匹配。
- `messages` 中的图像共享同样的 4 MiB 预算。超出会返回 `413`。

此路由接受最大 6,706,518 字节的正文。除图像外的一切仍必须适合 1 MiB。

### 流开始前的错误 {#chat-errors}

Phoenix 按此顺序检查正文大小、设置、紧急停止开关、访问密钥、账户、模型、图像和消息。第一次失败会以 HTTP 状态返回。[正文错误](guide:topic.adminApi#bodies)也适用。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 413 | `request_too_large` | `adminError.bodyTooLarge` | 正文或图像超出其限制。 |
| 404 | `not_found` | `adminError.setupMissing` | Phoenix 尚未设置。 |
| 503 | `service_unavailable` | `adminError.serviceDisabled` | 紧急停止开关已开启。 |
| 503 | `service_unavailable` | `adminError.setupIncomplete` | Phoenix 仍在启动。 |
| 503 | `service_unavailable` | `adminError.serviceStopping` | Home Assistant 正在关闭。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有访问密钥具有此 ID。 |
| 400 | `invalid_request` | `adminError.accessKeyInactive` | 访问密钥不活动。 |
| 404 | `not_found` | `adminError.providerNotConfigured` | 没有提供商账户具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerModelMissing` | 账户没有选择模型。 |
| 400 | `invalid_request` | `adminError.attachmentsInvalid`、`adminError.attachmentsTooMany`、`adminError.attachmentTooLarge`、`adminError.attachmentUnsupported`、`adminError.attachmentDimensions`、`adminError.attachmentsTooLarge` | 图像无法读取，或违反某条[图像限制](guide:#chat-images)。 |
| 400 | `invalid_request` | `adminError.attachmentsContinue` | 图像与 `continue: true` 一起发送。图像只能随新消息发送。 |
| 400 | `invalid_request` | `adminError.attachmentsCompanion` | 图像与 Home Companion 密钥一起发送，而它只接受文本。 |
| 400 | `invalid_request` | `adminError.attachmentsVisionUnsupported` | 模型不接受图像。 |
| 400 | `invalid_request` | `adminError.invalidHostHeader` | 请求的 `Host` 标头无法构成 URL。 |
| 400 | `invalid_request` | `adminError.nothingToContinue` | 发送了 `continue: true`，但没有可继续的对话。 |
| 400 | `invalid_request` | `adminError.emptyMessage` | 消息为空，且没有图像。 |

## 事件流 {#events}

开始的轮次返回 `200`，`Content-Type` 为 `text/event-stream`，并带有 `X-Phoenix-Request-ID` 头。每帧是一个事件名和一个 JSON 对象：

```
event: assistant_delta
data: {"text": "Turning off the kitchen lights."}
```

Phoenix 每 15 秒发送一条 `: keepalive` 注释。流开始后，问题会以 `error` 事件到达，绝不以 HTTP 状态到达。标记为可选的字段仅在它们有值时出现。

| 事件 | 数据字段 | 说明 |
|---|---|---|
| `ready` | `provider`（类型）、`model`、`access_key_id` | 始终是第一个事件。 |
| `status` | `provider`（标签）、`step`、`tools`、`phase`、`seconds`；重试期间还有 `attempt`、`detail`、`detail_code` | `phase` 为 `waiting`、`thinking` 或 `retrying`。在模型调用开始时、每次变化时，以及没有其他内容到达时每 30 秒发送。 |
| `thinking_delta` | `text` | 推理文本，仅在提供商流式发送时。 |
| `assistant_delta` | `text` | 回复文本。 |
| `focus_declined` | 无 | 在家庭专注模式下，模型拒绝了关于家之外的请求。Phoenix 会从回复中移除模型的标记。 |
| `tool_call` | `id`、`name`、`arguments` | 在工具运行前发送。 |
| `tool_progress` | `id`、`message`；可选 `message_key`、`message_params` | 长时间运行工具的实时状态，最多每 2 秒一次。 |
| `approval_required` | `approval_id`、`tool_name`、`review_url`、`expires_at`、`diff` | 轮次最多等待 900 秒以获取决定。 |
| `approval_resolved` | `approval_id`、`status`、`reason`；可选 `reason_kind` | 当已批准的操作失败时，`reason_kind` 为 `execution`。 |
| `tool_image` | `id`、`name`、`index`、`mime_type`、`data` | 来自工具结果的 base64 图像。 |
| `tool_result` | `id`、`name`、`is_error`、`summary` | `summary` 会缩短以供显示。 |
| `usage` | `input_tokens`、`output_tokens`、`context_tokens`、`cache_read_tokens`、`cache_write_tokens` | 提供商到此轮次为止的总量，仅在报告用量时。 |
| `notice` | `code`、`message` | `code` 为 `bad_tool_call_retry`、`max_tokens_stop` 或 `empty_turn`。 |
| `continue_required` | `iterations` | 轮次在限制处暂停。使用 `continue: true` 发送下一个请求。 |
| `error` | `code`、`message`；可选 `retryable`、`outcome`、`diagnostic`、`message_params`、`provider_detail`、`provider_code`、`rate_limit`、`refused` | `code` 包括 `cancelled`、`internal`、`kill_switch`、`unauthorized`、`review_changed`、`outcome_unknown`、`output_limit`、`invalid_request` 以及提供商自己的代码。 |
| `messages` | `messages` | 完整更新后的转录。保留它用于下一轮次。 |
| `done` | `stop_reason` | 始终是最后一个事件。失败的轮次后，`stop_reason` 为 `error`。 |

相关：[审批与变更端点](guide:topic.adminApiApprovals)、[{{label:agentchat.title}}](guide:window.agentChat#approvals)
