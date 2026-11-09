---
{ "id": "topic.adminApiSettings", "title": "设置与系统端点", "parent": "topic.adminApi", "order": 50, "keywords": ["设置", "配置", "系统", "清除", "重置", "信息", "版本", "目录", "翻译"], "sectionKeywords": { "settings": ["PATCH", "设置对象"], "settings-read": ["计算字段", "tool_routing_qualified_languages", "esphome_builder"], "settings-change": ["校验", "副作用"], "writable-settings": ["默认", "范围", "钳制", "kill_switch", "mesa_mode"], "api-only": ["shadow", "工具审核", "observe", "advise", "local_shadow", "hybrid", "command_routing_languages", "command_routing_timeout_ms", "decision_shadow_enabled"], "wipe": ["删除全部", "恢复出厂设置", "WIPE", "wipe_core", "wipe_providers", "wipe_mesa"], "confirmations": ["确认", "确认字段"], "info": ["版本", "工具数量", "home_companion_reach"], "catalog": ["语言", "区域设置", "字符串", "翻译"], "audit": ["请求日志", "审计日志", "筛选"], "conversations": ["对话日志", "转录", "历史"], "voice-pipeline": ["Assist 管道", "语音助手", "首选助手"], "ai-task": ["AI 任务", "数据生成", "默认实体"], "routing": ["命令路由", "识别", "预览", "统计", "请求预算", "决策提供商"] } }
---
使用这些端点读取和更改 Phoenix 的全局设置、清除其数据，以及读取其日志、版本和路由计数。每个端点都遵循[管理 API](guide:topic.adminApi)概述中的规则。

相关：[错误](guide:topic.adminApi#errors)、[Agent Chat 端点](guide:topic.adminApiChat)

## 设置 {#settings}

```
GET    /api/phoenix-mcp/admin/settings
PATCH  /api/phoenix-mcp/admin/settings
```

读取和更改设置工作区背后的设置。每项设置对操作员意味着什么，请参见其窗口页面：[常规](guide:window.settingsGeneral)、[安全](guide:window.settingsSafety)、[日志](guide:window.settingsLogs)、[Agent Chat](guide:window.settingsChat)、[Home Assistant](guide:window.settingsHome) 和 [AI 提供商](guide:window.settingsProviders)。

### 读取设置 {#settings-read}

`GET /api/phoenix-mcp/admin/settings` 返回每项[可写设置](guide:#writable-settings)以及这些只读字段：

- `tool_routing_qualified_languages`
- `assist_api_supported`
- `voice_agent_pipeline_supported`
- `voice_agent_pipeline_preferred`
- `voice_agent_pipeline_id`，仅由[语音智能体助手](guide:#voice-pipeline)路由设置
- `ai_task_supported`
- `ai_task_decisions_supported`
- `esphome_integration`、`esphome_builder` 和 `esphome_builder_live`

### 更改设置 {#settings-change}

`PATCH /api/phoenix-mcp/admin/settings` 接受可写设置的任意子集。没有设置需要确认字段，`kill_switch` 也不例外。

Phoenix 在写入任何内容之前检查每个值。第一个问题会拒绝整个请求，不会发生任何更改。

接受的更改会产生这些效果：

- 它会取消正在进行的决策工作，并使进行中的路由结果和命令审批失效。
- 开启 `access_key_presets_enabled` 会给每个密钥一个“默认”预设。
- 关闭 `kill_switch` 会重新打开智能体路由。
- Phoenix 会立即应用 `mesa_mode`，并更新语音智能体、AI 任务和 Assist 工具提供商。
- `mesa_inject_enabled` 和 `agentcli_global` 会在页面下次完整加载时生效。

审计日志会记录更改，包含已更改设置的名称，而不是其值。响应与读取相同。当语音智能体不再完全设置好，但 Home Assistant 保留 Phoenix 的助手作为其唯一助手时，它会添加 `voice_agent_pipeline_kept: true`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.unknownFields` | 某个字段不可写，例如 `voice_agent_pipeline_id`。`message_params` 包含 `fields`。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | 某个布尔设置收到了其他类型。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.valueAllowed` | 某个选择设置收到了其他值：`mesa_mode`、某种风格、某个详细级别、某种路由模式或某个审计菜单。`message_params` 包含 `field` 和 `values`。 |
| 400 | `invalid_request` | `adminError.integerRequired` | 某个整数设置收到了其他类型。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.routingLanguagesInvalid` | `command_routing_languages` 指定了不是已安装 Assist 语言的语言。 |
| 400 | `invalid_request` | `adminError.conversationLimitInvalid` | 对话日志限制、`command_routing_timeout_ms`、`decision_timeout_ms` 或 `decision_request_budget` 超出范围。`message_params` 包含 `field`、`min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.accessKeyIdOrNull` | 某个访问密钥设置不是字符串或 `null`。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.activeAccessKeyRequired` | 某个访问密钥设置指定的不是活动密钥。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.stringOrNull` | 某个模型设置不是字符串或 `null`。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.configuredProviderRequired` | 某个提供商设置指定的账户不存在。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.decisionProviderRequired` | 在没有决策账户的情况下开启了某个决策功能。 |
| 400 | `invalid_request` | `adminError.toolRoutingUnqualified` | 为没有合格语言的模型把工具审核模式设为 `advise`。 |

### 可写设置 {#writable-settings}

“拒绝”表示超出范围的值返回 `400`。“钳制”表示 Phoenix 将其移到最近的边界。“决策账户”是决策类型的账户，“对话账户”是其他任何账户。

| 设置 | 类型和范围 | 默认值 | 说明 |
|---|---|---|---|
| `kill_switch` | boolean | `false` | 关闭它会打开智能体路由。开启时，聊天路由返回 `503`。参见[紧急停止开关](guide:window.settingsSafety#kill-switch)。 |
| `disable_all_logging` | boolean | `false` | 面板以相反方式显示它，即 **{{label:settings.logEnableLabel}}**。参见[日志](guide:window.settingsLogs#log-enable)。 |
| `log_allowed` | boolean | `true` | 参见[日志](guide:window.settingsLogs#log-allowed)。 |
| `log_denied` | boolean | `true` | 参见[日志](guide:window.settingsLogs#log-denied)。 |
| `log_rate_limited` | boolean | `true` | 参见[日志](guide:window.settingsLogs#log-rate-limited)。 |
| `log_entity_names` | boolean | `true` | 参见[日志](guide:window.settingsLogs#log-entity-ids)。 |
| `log_client_ip` | boolean | `true` | 参见[日志](guide:window.settingsLogs#log-client-ip)。 |
| `notify_on_rate_limit` | boolean | `false` | 参见[日志](guide:window.settingsLogs#notify-rate-limit)。 |
| `notify_on_approval` | boolean | `true` | 参见[日志](guide:window.settingsLogs#notify-approval)。 |
| `audit_flush_interval` | integer：0、5、10、15、30 或 60 分钟 | 15 | 否则拒绝。接受数字字符串。参见[日志](guide:window.settingsLogs#log-flush)。 |
| `audit_log_maxlen` | integer：100、1000、5000 或 10000 | 10000 | 否则拒绝。接受数字字符串。参见[日志](guide:window.settingsLogs#log-max-entries)。 |
| `conversation_log_enabled` | boolean | `false` | 参见[日志](guide:window.settingsLogs#conversation-log)。 |
| `conversation_log_max_entries` | integer，1 到 2000 | 100 | 拒绝。需要 JSON 整数。参见[日志](guide:window.settingsLogs#conversation-entries)。 |
| `conversation_log_max_days` | integer，1 到 365 | 7 | 拒绝。需要 JSON 整数。参见[日志](guide:window.settingsLogs#conversation-days)。 |
| `conversation_log_max_mb` | integer，1 到 250（十进制 MB） | 25 | 拒绝。需要 JSON 整数。参见[日志](guide:window.settingsLogs#conversation-size)。 |
| `mesa_mode` | `off`、`advisory` 或 `enforced` | `advisory` | 参见[安全](guide:window.settingsSafety#mesa-mode)。 |
| `mesa_inject_enabled` | boolean | `false` | 在页面下次完整加载时生效。参见[常规](guide:window.settingsGeneral#inject)。 |
| `access_key_presets_enabled` | boolean | `false` | 开启会给每个密钥一个“默认”预设。参见[常规](guide:window.settingsGeneral#presets)。 |
| `agentcli_global` | boolean | `true` | 在页面下次完整加载时生效。参见[Agent Chat](guide:window.settingsChat#chat-global)。 |
| `agentcli_memory_chars` | integer，0 到 960000 | 200000 | 钳制。接受数字字符串。参见[Agent Chat](guide:window.settingsChat#chat-memory)。 |
| `agentcli_max_iterations` | integer，3 到 100 | 20 | 钳制。参见[Agent Chat](guide:window.settingsChat#chat-steps)。 |
| `agentcli_conversation_style` | `direct`、`warm`、`calm_guide`、`lively` 或 `technical` | `direct` | 参见[Agent Chat](guide:window.settingsChat#chat-style)。 |
| `agentcli_detail_level` | `concise`、`balanced` 或 `detailed` | `concise` | 参见[Agent Chat](guide:window.settingsChat#chat-detail)。 |
| `agentcli_home_focused` | boolean | `false` | 偏好，不是安全控制。参见[Agent Chat](guide:window.settingsChat#chat-home-focused)。 |
| `agentcli_routing_mode` | `off`、`auto`、`local` 或 `hybrid` | `off` | 面板只写入 `off` 和 `auto`。`hybrid` 需要决策账户。参见[Agent Chat](guide:window.settingsChat#chat-commands)和[仅 API 设置](guide:#api-only)。 |
| `agentcli_routing_shadow_enabled` | boolean | `false` | 仅 API。参见[仅 API 设置](guide:#api-only)。 |
| `voice_agent_routing_mode` | `off`、`auto`、`local_shadow`、`local` 或 `hybrid` | `off` | 面板只写入 `off` 和 `auto`。参见[Home Assistant](guide:window.settingsHome#voice-commands)和[仅 API 设置](guide:#api-only)。 |
| `command_routing_languages` | 路由语言标签列表，无重复 | `["en"]` | 仅 API。空列表会暂停使用它的模式中的识别。参见[仅 API 设置](guide:#api-only)。 |
| `command_routing_timeout_ms` | integer，50 到 5000 | 500 | 仅 API。拒绝。需要 JSON 整数。 |
| `decision_provider_id` | 决策账户 ID 或 `null` | `null` | 正在使用的决策账户。设置它也会设置默认值。`null` 会关闭影子观察、所有工具审核、决策和每个 `hybrid` 模式。参见[AI 提供商](guide:window.settingsProviders#decision-provider)。 |
| `decision_provider_default_id` | 决策账户 ID 或 `null` | `null` | 选定的默认值，在决策提供商关闭时保留。在正在使用账户时设置它会切换到该账户。 |
| `decision_shadow_enabled` | boolean | `false` | 仅 API。开启它需要决策账户。参见[仅 API 设置](guide:#api-only)。 |
| `decision_timeout_ms` | integer，250 到 30000 | 5000 | 拒绝。需要 JSON 整数。参见[AI 提供商](guide:window.settingsProviders#decision-timeout)。 |
| `decision_request_budget` | integer，每小时 0 到 100000 | 0 | 0 表示不限。拒绝。需要 JSON 整数。参见[AI 提供商](guide:window.settingsProviders#decision-request-limit)。 |
| `agentcli_tool_routing_mode` | `off`、`observe` 或 `advise` | `off` | 仅 API。参见[仅 API 设置](guide:#api-only)。 |
| `voice_agent_tool_routing_mode` | `off`、`observe` 或 `advise` | `off` | 仅 API。参见[仅 API 设置](guide:#api-only)。 |
| `ai_task_tool_routing_mode` | `off`、`observe` 或 `advise` | `off` | 仅 API。参见[仅 API 设置](guide:#api-only)。 |
| `provider_catalog_refresh_enabled` | boolean | `true` | 参见[AI 提供商](guide:window.settingsProviders#provider-checks)。 |
| `provider_catalog_refresh_hours` | integer，6 到 720 | 24 | 钳制。参见[AI 提供商](guide:window.settingsProviders#provider-checks-hours)。 |
| `assist_bound_access_key_id` | 活动访问密钥 ID 或 `null` | `null` | 空字符串也会清除它。参见[Home Assistant](guide:window.settingsHome#assist-access-key)。 |
| `voice_agent_enabled` | boolean | `false` | 参见[Home Assistant](guide:window.settingsHome#voice-enable)。 |
| `voice_agent_access_key_id` | 活动访问密钥 ID 或 `null` | `null` | 参见[Home Assistant](guide:window.settingsHome#voice-access-key)。 |
| `voice_agent_provider_id` | 对话账户 ID 或 `null` | `null` | 参见[Home Assistant](guide:window.settingsHome#voice-provider)。 |
| `voice_agent_model` | string 或 `null` | `null` | Phoenix 不会对照提供商检查它。参见[Home Assistant](guide:window.settingsHome#voice-model)。 |
| `voice_agent_conversation_style` | 同 `agentcli_conversation_style` | `direct` | 参见[Home Assistant](guide:window.settingsHome#voice-style)。 |
| `voice_agent_detail_level` | 同 `agentcli_detail_level` | `concise` | 参见[Home Assistant](guide:window.settingsHome#voice-detail)。 |
| `voice_agent_home_focused` | boolean | `false` | 参见[Home Assistant](guide:window.settingsHome#voice-home-focused)。 |
| `ai_task_enabled` | boolean | `false` | 参见[Home Assistant](guide:window.settingsHome#ai-task-enable)。 |
| `ai_task_access_key_id` | 活动访问密钥 ID 或 `null` | `null` | 参见[Home Assistant](guide:window.settingsHome#ai-task-access-key)。 |
| `ai_task_decisions_enabled` | boolean | `false` | 需要正在使用决策账户。参见[Home Assistant](guide:window.settingsHome#ai-decisions-enable)。 |
| `ai_task_decisions_access_key_id` | 活动访问密钥 ID 或 `null` | `null` | 参见[Home Assistant](guide:window.settingsHome#ai-decisions-access-key)。 |
| `ai_task_provider_id` | 对话账户 ID 或 `null` | `null` | 参见[Home Assistant](guide:window.settingsHome#ai-task-provider)。 |
| `ai_task_model` | string 或 `null` | `null` | 参见[Home Assistant](guide:window.settingsHome#ai-task-model)。 |
| `ai_task_conversation_style` | 同 `agentcli_conversation_style` | `direct` | 参见[Home Assistant](guide:window.settingsHome#ai-task-style)。 |
| `ai_task_detail_level` | 同 `agentcli_detail_level` | `balanced` | 参见[Home Assistant](guide:window.settingsHome#ai-task-detail)。 |

## 没有面板控件的设置 {#api-only}

面板没有这些设置和值的控件。只能通过 API 设置它们。

- `agentcli_routing_mode` 的 `local` 和 `hybrid`：面板都显示为开启。`hybrid` 需要决策账户。
- `voice_agent_routing_mode` 的 `local_shadow`、`local` 和 `hybrid`。使用决策账户时，`auto` 本身按 `hybrid` 工作；否则按 `local` 工作。
- `agentcli_routing_shadow_enabled`：在聊天路由关闭时，仅以观察方式运行 Agent Chat 命令识别。任何把任一路由模式设为 `off` 或 `auto` 的更改都会关闭它。
- `command_routing_languages`：用于 `local_shadow`、`local`、`hybrid` 和后台观察的语言，来自已安装的 Assist 句子语言加上 `zh-Hans` 和 `zh-Hant`。`auto` 则使用每个已安装的 Assist 语言。
- `command_routing_timeout_ms`：本地识别可以花费的时间，以毫秒为单位。
- `decision_shadow_enabled`：把识别遗漏的语音智能体命令发送给决策提供商，仅作为观察。它只能与 `voice_agent_routing_mode` 的 `local` 或 `local_shadow` 一起工作。

三种工具审核模式要求决策提供商在模型提议的每次工具调用运行前检查它。它们是 `agentcli_tool_routing_mode`、`voice_agent_tool_routing_mode` 和 `ai_task_tool_routing_mode`。

- `observe` 和 `advise` 需要决策账户。
- `advise` 还需要合格语言。`tool_routing_qualified_languages` 列出它们；为空时，Phoenix 会以 `adminError.toolRoutingUnqualified` 拒绝 `advise`。
- Home Companion 密钥跳过工具审核。

相关：[命令如何路由](guide:topic.commandRouting)

## 清除数据 {#wipe}

```
DELETE /api/phoenix-mcp/admin/wipe
```

按范围删除 Phoenix 的数据。此操作无法撤销。

```json
{"confirm": "WIPE", "wipe_core": true, "wipe_providers": true, "wipe_mesa": false}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `confirm` | string | 必填。必须完全为 `"WIPE"`。 |
| `wipe_core` | boolean | 默认 `true`。 |
| `wipe_providers` | boolean | 默认 `true`。 |
| `wipe_mesa` | boolean | 默认 `false`。 |

缺少标志，或标志不是布尔值，会使用其默认值。因此仅 `{"confirm": "WIPE"}` 就会清除核心数据和提供商账户。

`wipe_core` 会清除：

- 访问密钥、已归档密钥、实体提示，以及所有重置为默认值的设置。
- 待处理审批。Phoenix 会消除其通知并报告它们已解决。
- 审计日志、对话日志、版本历史，以及结果未知的智能体写入记录。
- 路由统计、决策请求计数、卡片目录缓存和路由澄清。
- 速率限制计数、请求计数和通知标记。
- 每个密钥的传感器和过期计时器。Phoenix 还会恢复智能体限时更改的集成日志级别。
- Phoenix Assist 管道。

之后，Phoenix 会应用每个已更改的默认值，例如 MESA 模式、语音智能体、AI 任务和添加到 Home Assistant 页面的模块。如果智能体路由已关闭，它会打开它们。

`wipe_providers` 会清除每个 Agent Chat 和决策账户及其 API 密钥。它还会清除使用它们的设置。即语音智能体和 AI 任务的提供商与模型，以及正在使用的决策账户及其默认值。影子观察、工具审核和每个 `hybrid` 模式也会关闭。

`wipe_mesa` 会清除每条 MESA 策略和已忽略的建议。

没有哪个标志会清除配置恢复记录、日历写入日志或仪表盘资源记录。请使用单独的端点移除已解决的恢复记录。

响应为 `204`，没有正文。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.wipeConfirmationRequired` | `confirm` 不完全是 `"WIPE"`。 |
| 409 | `conflict` | `adminError.approvalWipeInProgress` | 有审批正在运行。请等待它完成。仅在使用 `wipe_core` 时。 |
| 409 | `conflict` | `adminError.approvalWipeExecutionUncertain` | 某个审批可能已应用但未回报。请先检查其结果。仅在使用 `wipe_core` 时。 |
| 503 | `service_unavailable` | `adminError.mesaSaveFailed` | Phoenix 无法保存 MESA 更改。仅限 `wipe_mesa`。 |

相关：[安全设置](guide:window.settingsSafety#data-management)

### 确认字段 {#confirmations}

只有三个路由在正文中要求确认。设置 PATCH 不要求任何确认。

| 路由 | 正文 |
|---|---|
| [清除数据](guide:#wipe) | `{"confirm": "WIPE"}` 加上可选的范围标志 |
| [清除对话日志](guide:#clear-conversations) | 完全为 `{"confirm": true}` |
| [重置路由统计](guide:#reset-statistics) | 完全为 `{"surface": "voice", "confirm": true}`，或 `"chat"`、`"ai_task"` |

## 版本和工具 {#info}

```
GET    /api/phoenix-mcp/admin/info
```

返回 Phoenix 的版本以及它向智能体提供什么。

| 字段 | 说明 |
|---|---|
| `version` | Phoenix 的版本。 |
| `min_ha_version` | Phoenix 支持的最旧 Home Assistant 版本。 |
| `github_url` | 项目仓库。 |
| `tool_count` | `total`、`native` 和 `additional`，按完整工具目录计数，与开启了哪些功能无关。 |
| `home_companion_reach` | Home Companion 密钥的 `control_domains`、`read_domains` 和 `sensor_classes`。 |

## 面板字符串 {#catalog}

```
GET    /api/phoenix-mcp/admin/catalog/{language}
```

返回面板某种语言的字符串。与所有其他端点不同，它在 Phoenix 仍在启动时就会响应。

响应为 `{"language": "<code>", "resources": {...}}`，使用扁平的点分键。Phoenix 会选择它提供的最接近语言，然后回退到英文。未知语言会以英文返回，`"language": "en"`。未翻译或占位符不匹配的字符串会以英文返回。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.catalogNotFound` | 找不到目录。仅当英文目录缺失时才会发生。 |

## 审计日志 {#audit}

```
GET    /api/phoenix-mcp/admin/audit
```

分页读取智能体请求日志，最新的在前。

| 字段 | 类型 | 说明 |
|---|---|---|
| `limit` | integer | 默认 100，最多 500。 |
| `offset` | integer | 参见[请求体](guide:topic.adminApi#bodies)。 |
| `access_key_id` | string | 精确匹配。 |
| `outcome` | string | 精确匹配：`allowed`、`denied`、`not_found`、`rate_limited`、`not_implemented`、`invalid_request`、`pending_approval`、`cancelled` 或 `unknown`。 |
| `ip` | string | 客户端地址的一部分。 |
| `method` | string | 方法的一部分，不区分大小写。 |
| `resource` | string | 资源的一部分，不区分大小写。 |
| `request_id` | string | 精确匹配。 |
| `since` | string | ISO 时间。没有时区的时间按本地时间计算。 |

响应包含 `entries` 和 `total`。每个条目有 `request_id`、`timestamp`、`access_key_id`、`access_key_name`、`method`、`resource`、`outcome`、`client_ip` 和 `pass_through`。它还可能包含 `payload`、`mesa_advisory`、`preset`、`stale_tools_advisory`、`ha_user_id` 和 `originating_access_key_id`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination`、`adminError.paginationNonNegative` | `limit` 或 `offset` 不是大于等于 0 的整数。 |
| 400 | `invalid_request` | `adminError.sinceTimestampInvalid` | `since` 不是有效时间。 |
| 400 | `invalid_request` | `adminError.outcomeFilterUnknown` | `outcome` 不是上面的值之一，或为空。`message_params` 包含 `value`。 |

相关：[{{label:shell.tabAudit}}](guide:window.auditLog)、[日志记录](guide:topic.logRecords)

## 对话日志 {#conversations}

```
GET    /api/phoenix-mcp/admin/conversations
DELETE /api/phoenix-mcp/admin/conversations
GET    /api/phoenix-mcp/admin/conversations/{entry_id}
```

读取和清除已记录的对话。使用 `conversation_log_enabled` 开启记录。

相关：[对话日志](guide:window.conversationLog)

### 列出对话 {#list-conversations}

`GET /api/phoenix-mcp/admin/conversations`

| 字段 | 类型 | 说明 |
|---|---|---|
| `limit` | integer | 默认 25，最多 100。 |
| `offset` | integer | 参见[请求体](guide:topic.adminApi#bodies)。 |
| `search` | string | 最多 512 个字符。匹配已记录内容，不区分大小写。 |
| `source` | string | 最多 64 个字符：`mcp`、`agentcli`、`assist`、`voice`、`ai_task` 或 `home_companion`。 |
| `outcome` | string | 最多 64 个字符。一个状态值。`failed` 和 `denied` 也匹配结果混合的轮次。 |
| `since` | string | ISO 时间。没有时区的时间按本地时间计算。 |
| `grouped` | string | 为分组行设为 `true`。 |

响应包含 `entries`、`total`、`enabled`、`available`、`bytes` 和 `max_bytes`。条目最新的在前。

每个条目有 `id`、`source`、`started_at`、`updated_at`、`finished_at`、`coverage`、`status` 和 `execution_status`。它还有 `response_status`、`tool_count`、`change_count`、`omissions`、`omitted_events`、`first_tool`、`excerpt` 和 `prompt_attachments`。

分组条目还有 `latest_at`。Agent Chat 线程会添加 `turns`（最早的在前）和 `history_missing`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination`、`adminError.paginationNonNegative` | `limit` 或 `offset` 不是大于等于 0 的整数。 |
| 400 | `invalid_request` | `adminError.sinceTimestampInvalid` | `since` 不是有效时间。 |

### 打开对话 {#open-conversation}

`GET /api/phoenix-mcp/admin/conversations/{entry_id}` 返回完整的已存储记录，包含其事件以及任何内容被省略的原因。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.conversationNotFound` | 没有对话具有此 ID，或它已过期。 |

### 清除对话日志 {#clear-conversations}

`DELETE /api/phoenix-mcp/admin/conversations` 只接受 `{"confirm": true}`，并返回 `{"cleared": true}`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.conversationClearConfirm` | 正文不完全是 `{"confirm": true}`。 |
| 503 | `service_unavailable` | `adminError.conversationStorageUnavailable` | 日志已在内存中清除，但保存失败。请重试。 |

## 语音智能体助手 {#voice-pipeline}

```
POST   /api/phoenix-mcp/admin/voice_agent/pipeline
DELETE /api/phoenix-mcp/admin/voice_agent/pipeline
POST   /api/phoenix-mcp/admin/voice_agent/pipeline/preferred
DELETE /api/phoenix-mcp/admin/voice_agent/pipeline/preferred
```

创建和移除使用 Phoenix 语音智能体的 Home Assistant 助手，并选择它是否为首选。

相关：[Home Assistant 设置](guide:window.settingsHome#voice-assistant)

### 创建助手 {#create-pipeline}

`POST /api/phoenix-mcp/admin/voice_agent/pipeline`

| 字段 | 类型 | 说明 |
|---|---|---|
| `preferred` | boolean | 可选。默认 `true`。使助手成为 Home Assistant 的首选助手。 |

请先完整设置语音智能体：已启用，并有访问密钥、提供商和模型。如果 Phoenix 的助手仍然存在，Phoenix 会复用它。

响应包含 `pipeline_id`、`name` 和 `preferred`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.booleanRequired` | `preferred` 不是布尔值。 |
| 404 | `not_found` | `adminError.setupMissing` | Phoenix 尚未设置。 |
| 400 | `invalid_request` | `adminError.assistPipelineUnavailable` | Home Assistant 没有助手支持。 |
| 400 | `invalid_request` | `adminError.voiceAgentNotConfigured` | 语音智能体尚未完全设置并开启。 |
| 400 | `invalid_request` | `adminError.assistPipelineFailed` | 创建助手失败。 |

### 移除助手 {#remove-pipeline}

`DELETE /api/phoenix-mcp/admin/voice_agent/pipeline` 会在需要时把首选权移到另一个助手，然后删除 Phoenix 的助手。响应为 `{"ok": true, "kept": false}`。

当 Home Assistant 仍保留该助手时，`kept` 为 `true`，因为它已经是唯一助手，或删除失败。

### 首选助手 {#preferred-pipeline}

`POST` 让 Phoenix 的助手成为首选，`DELETE` 停止它作为首选但保留它。两者都不接受正文。响应包含 `pipeline_id` 和 `preferred`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.assistPipelineUnavailable` | Home Assistant 没有助手支持。 |
| 400 | `invalid_request` | `adminError.assistantSetupFirst` | 还没有 Phoenix 助手。 |
| 400 | `invalid_request` | `adminError.assistantOnlyOneStaysPreferred` | Phoenix 的是唯一助手，因此它保持首选。 |
| 400 | `invalid_request` | `adminError.assistantPreferredFailed` | 保存首选设置失败。 |

## AI 任务默认值 {#ai-task}

```
GET    /api/phoenix-mcp/admin/ai_task/preferred
POST   /api/phoenix-mcp/admin/ai_task/preferred
DELETE /api/phoenix-mcp/admin/ai_task/preferred
GET    /api/phoenix-mcp/admin/ai_task/evaluate_preferred
POST   /api/phoenix-mcp/admin/ai_task/evaluate_preferred
DELETE /api/phoenix-mcp/admin/ai_task/evaluate_preferred
```

读取和设置 Home Assistant 的默认数据生成实体。`POST` 让 Phoenix 的 AI 任务实体成为默认，替换其他任何实体。`DELETE` 仅在默认实体是 Phoenix 的时候清除它。

三者都返回相同字段：

| 字段 | 说明 |
|---|---|
| `supported` | Home Assistant 是否支持 AI 任务。 |
| `entity_id` | Phoenix 的 AI 任务实体，或在 AI 任务完全设置好之前为 `null`。 |
| `gen_data_entity_id` | 当前默认实体。 |
| `gen_data_name` | 当前默认实体的名称。 |
| `is_preferred` | Phoenix 的实体是否为默认。 |

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.setupMissing` | Phoenix 尚未设置。 |
| 400 | `invalid_request` | `adminError.aiTaskNotConfigured` | 仅限 `POST`。Phoenix 的 AI 任务尚未完全设置并开启。 |
| 400 | `invalid_request` | `adminError.aiTaskPreferencesUnavailable` | 仅限 `POST`。Home Assistant 没有保存默认值的位置。 |
| 400 | `invalid_request` | `adminError.aiTaskPreferredFailed` | 仅限 `POST`。保存默认值失败。 |

相关：[Home Assistant 设置](guide:window.settingsHome#ai-task-default)

## 命令路由 {#routing}

```
GET    /api/phoenix-mcp/admin/command-routing/preview
POST   /api/phoenix-mcp/admin/command-routing/preview
GET    /api/phoenix-mcp/admin/command-routing/statistics
DELETE /api/phoenix-mcp/admin/command-routing/statistics
```

测试 Phoenix 会如何路由一个句子，并读取路由计数和总量。

相关：[命令如何路由](guide:topic.commandRouting)、[阅读路由统计](guide:topic.routingStatistics)

### 实时计数器 {#routing-counters}

`GET /api/phoenix-mcp/admin/command-routing/preview` 返回保存在内存中的计数器。Phoenix 重启时它们从零开始。

| 字段 | 说明 |
|---|---|
| `observations`、`candidates`、`reasons`、`mean_elapsed_ms`、`agent_chat` | 语音识别计数器。 |
| `decision_shadow` | 决策计数器：`observations`、`reasons`、`choices`、`busy`、`requests`、`tool_routing`、`mean_elapsed_ms`、`hybrid`、`chat_hybrid` 和 `agent_chat`。 |
| `request_budget` | `limit`、`used`、`remaining`（不限时为 `null`）、`reset_at` 和 `available`。`reset_at` 为 Unix 秒，位于下一个 UTC 小时的开始。 |

### 测试句子 {#routing-preview}

`POST /api/phoenix-mcp/admin/command-routing/preview` 显示 Phoenix 会如何路由一个句子，而不运行它。它不计入观察。

| 字段 | 类型 | 说明 |
|---|---|---|
| `access_key_id` | string | 必填。一个活动访问密钥。 |
| `sentence` | string | 必填。不能为空，最多 512 个字符。 |
| `language` | string | 默认 `en`。任何已安装的 Assist 语言，包括地区和中文文字变体。 |
| `decision` | boolean | 还会询问决策提供商。 |
| `targets` | boolean | 需要 `decision`。 |
| `batch` | boolean | 需要 `decision` 和 `targets`，且不能有 `messages`。 |
| `numeric` | boolean | 需要 `batch`。 |
| `messages` | list | 更早的轮次，用于历史审核。需要 `decision`，且不能有 `targets`。 |
| `references` | boolean | 需要 `messages`。 |

不允许其他字段。任何需要 `decision` 的选项都会联系决策账户，并计入 `decision_request_budget`。

响应包含 `route`（`local_candidate` 或 `provider`）、`reason`、`proposal`、`executed: false`、`elapsed_ms`、`voice_observations` 和 `decision_observations`。根据选项，它还可能包含 `decision_shadow`、`batch_review` 或 `context_review`。

如果测试期间密钥或设置发生变化，结果为 `route: provider`，`reason: authority_changed`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.routingPreviewInvalid` | 某个字段缺失、过长、类型错误、不允许，或组合不允许。 |
| 400 | `invalid_request` | `adminError.activeAccessKeyRequired` | `access_key_id` 指定的不是活动密钥。`message_params` 包含 `field`。 |

### 路由统计 {#routing-statistics}

`GET /api/phoenix-mcp/admin/command-routing/statistics` 返回已保存的总量，跨重启保留。响应包含 `voice`、`chat` 和 `ai_task`（“评估”操作），各含：

- `since`
- `outcomes`：`local`、`decision`、`provider`、`pending`、`failed`、`cancelled` 和 `refused`；`ai_task` 用 `decision` 表示回答，用 `refused` 表示拒绝
- `provider_turns`
- `decision_usage` 和 `provider_usage`，各含 `requests`、`reports`、`input_tokens` 和 `output_tokens`

### 重置统计 {#reset-statistics}

`DELETE /api/phoenix-mcp/admin/command-routing/statistics` 重置一个界面的总量：

```json
{"surface": "voice", "confirm": true}
```

把 `surface` 设为 `voice`、`chat` 或 `ai_task`。响应会显示重置后的所有界面。决策请求计数不会被重置。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.routingStatisticsResetInvalid` | 正文不完全是有效的 `surface` 加上 `confirm: true`。 |
