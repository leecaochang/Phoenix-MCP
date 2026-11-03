---
{ "id": "topic.adminApiKeys", "title": "访问密钥端点", "parent": "topic.adminApi", "order": 10, "keywords": ["访问密钥 API", "令牌 API", "phx_", "创建密钥", "权限 API", "能力 API"], "sectionKeywords": { "access-keys": ["访问密钥记录", "密钥列表"], "capability-fields": ["cap_", "deny", "allow", "confirm", "能力字段"], "key-create": ["新密钥", "access_key", "原始值", "201"], "key-change": ["PATCH", "角色", "备注", "重命名", "unknownFields"], "key-revoke": ["删除密钥", "吊销", "phoenix_mcp_access_key_revoked"], "key-rotate": ["轮换", "新值", "phoenix_mcp_access_key_rotated"], "key-stats": ["request_count", "denied_count", "rate_limit_hits"], "key-audit": ["审计行", "结果筛选", "客户端 IP"], "key-archived": ["已归档密钥", "已过期密钥", "已吊销密钥"], "presets": ["预设", "已保存设置", "access_key_presets_enabled"], "permissions": ["权限树", "节点", "域", "设备", "实体"], "permissions-replace": ["PUT", "替换树", "清除全部"], "permissions-node": ["单个节点", "设置状态"], "bulk-select": ["区域", "标签", "集成", "选择器"], "integration-options": ["配置条目", "集成列表"], "entity-hints": ["提示", "全局提示", "entity_hints"], "entities": ["实体树", "范围", "解析", "force_reload"], "resolve": ["resolution_path", "有效权限", "NO_ACCESS", "NOT_FOUND"], "scope": ["readable", "writable", "capability_flags"] } }
---
这些端点管理访问密钥：创建密钥、更改其设置和能力，以及设置每个密钥可以读取和控制什么。每个路径都以 `/api/phoenix-mcp/admin` 开头，并且需要管理员。

请求体、错误信封和 ID 规则如[管理 API](guide:topic.adminApi)所述。下面的错误表列出每个端点自己的错误。来自[请求体](guide:topic.adminApi#bodies)的正文错误适用于每个接受正文的端点。

相关：[错误](guide:topic.adminApi#errors)、[路径中的 ID](guide:topic.adminApi#ids)、[权限状态](guide:topic.adminApi#states)

## 访问密钥 {#access-keys}

```
GET    /api/phoenix-mcp/admin/access-keys
POST   /api/phoenix-mcp/admin/access-keys
GET    /api/phoenix-mcp/admin/access-keys/{id}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}
DELETE /api/phoenix-mcp/admin/access-keys/{id}
POST   /api/phoenix-mcp/admin/access-keys/{id}/rotate
GET    /api/phoenix-mcp/admin/access-keys/{id}/stats
GET    /api/phoenix-mcp/admin/access-keys/{id}/connection
GET    /api/phoenix-mcp/admin/access-keys/{id}/audit
GET    /api/phoenix-mcp/admin/access-keys/archived
DELETE /api/phoenix-mcp/admin/access-keys/archived/{id}
```

使用它们创建、更改、轮换和吊销密钥，以及读取其使用情况。`{id}` 是访问密钥的 ID。

每个指定密钥的端点在密钥不存在时返回 `404`：

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

### 访问密钥记录 {#key-record}

大多数端点返回密钥的记录。它绝不包含密钥本身。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 密钥的 ID。 |
| `name` | string | 密钥的名称。 |
| `created_at` | string | ISO 8601 时间。 |
| `created_by` | string | 创建它的管理员的 Home Assistant 用户 ID。 |
| `expires_at` | string 或 null | ISO 8601 时间，或 `null` 表示无有效期。 |
| `revoked` | boolean | |
| `last_used_at` | string 或 null | 智能体上次使用密钥的时间。 |
| `updated_at` | string | 密钥的最后更改。 |
| `pass_through` | boolean | 参见[直通模式](guide:topic.passThrough)。 |
| `use_assist_exposure` | boolean | 仅在 `pass_through` 为 `true` 时出现。 |
| `announce_all_tools` | boolean | |
| `confirm_inline_wait_seconds` | integer | 关闭时为 `0`。 |
| `rate_limit_requests` | integer | 每分钟请求数。`0` 会关闭速率限制。 |
| `rate_limit_burst` | integer | 每秒请求数。 |
| `persona` | string | 角色标签。 |
| `notes` | string 或 null | 给智能体的备注。 |
| `permissions` | object | 权限树，如[权限](guide:#permissions)所述。 |
| `presets` | array | 已保存的预设，如[预设](guide:#presets)所述。 |
| `active_preset_id` | string 或 null | 密钥匹配的预设。 |
| `cap_*` | string | 每项能力一个字段：`deny`、`allow` 或 `confirm`。 |

### 能力字段 {#capability-fields}

每个密钥都有这 32 个字段。每个字段接受 `deny`、`allow` 或 `confirm`，但只有标记为“是”的字段接受 `confirm`。

| 字段 | 接受 `confirm` | 能力 |
|---|---|---|
| `cap_config_read` | 否 | [{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read) |
| `cap_camera_read` | 否 | [{{label:caps.cap_camera_read.label}}](guide:window.capabilities#camera-read) |
| `cap_template_render` | 否 | [{{label:caps.cap_template_render.label}}](guide:window.capabilities#template-render) |
| `cap_log_read` | 否 | [{{label:caps.cap_log_read.label}}](guide:window.capabilities#log-read) |
| `cap_log_control` | 是 | [{{label:caps.cap_log_control.label}}](guide:window.capabilities#log-control) |
| `cap_search` | 否 | [{{label:caps.cap_search.label}}](guide:window.capabilities#search) |
| `cap_registry_read` | 否 | [{{label:caps.cap_registry_read.label}}](guide:window.capabilities#registry-read) |
| `cap_traces` | 否 | [{{label:caps.cap_traces.label}}](guide:window.capabilities#traces) |
| `cap_diagnostics` | 否 | [{{label:caps.cap_diagnostics.label}}](guide:window.capabilities#diagnostics) |
| `cap_broadcast` | 否 | [{{label:caps.cap_broadcast.label}}](guide:window.capabilities#broadcast) |
| `cap_notify` | 是 | [{{label:caps.cap_notify.label}}](guide:window.capabilities#notify) |
| `cap_service_response` | 否 | [{{label:caps.cap_service_response.label}}](guide:window.capabilities#service-response) |
| `cap_automation_write` | 是 | [{{label:caps.cap_automation_write.label}}](guide:window.capabilities#automation-write) |
| `cap_script_write` | 是 | [{{label:caps.cap_script_write.label}}](guide:window.capabilities#script-write) |
| `cap_blueprint_write` | 是 | [{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write) |
| `cap_scene_write` | 是 | [{{label:caps.cap_scene_write.label}}](guide:window.capabilities#scene-write) |
| `cap_helper_write` | 是 | [{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write) |
| `cap_calendar_write` | 是 | [{{label:caps.cap_calendar_write.label}}](guide:window.capabilities#calendar-write) |
| `cap_physical_control` | 是 | [{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control) |
| `cap_restart` | 是 | [{{label:caps.cap_restart.label}}](guide:window.capabilities#restart) |
| `cap_integration_write` | 是 | [{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write) |
| `cap_integration_reconfigure` | 是 | [{{label:caps.cap_integration_reconfigure.label}}](guide:window.capabilities#integration-reconfigure) |
| `cap_lovelace_write` | 是 | [{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write) |
| `cap_registry_write` | 是 | [{{label:caps.cap_registry_write.label}}](guide:window.capabilities#registry-write) |
| `cap_radio_write` | 是 | [{{label:caps.cap_radio_write.label}}](guide:window.capabilities#radio-write) |
| `cap_energy_write` | 是 | [{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write) |
| `cap_dashboard_resources` | 是 | [{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources) |
| `cap_backup` | 是 | [{{label:caps.cap_backup.label}}](guide:window.capabilities#backup) |
| `cap_filesystem` | 否 | [{{label:caps.cap_filesystem.label}}](guide:window.capabilities#filesystem) |
| `cap_yaml_edit` | 是 | [{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit) |
| `cap_esphome_yaml` | 否 | [{{label:caps.cap_esphome_yaml.label}}](guide:window.capabilities#esphome-yaml) |
| `cap_esphome_flash` | 是 | [{{label:caps.cap_esphome_flash.label}}](guide:window.capabilities#esphome-flash) |

相关：[拒绝访问、允许和确认](guide:window.capabilities#modes)

### 列出密钥 {#key-list}

`GET /access-keys` 返回活动密钥记录的裸 JSON 数组。已吊销和已过期的密钥在[归档](guide:#key-archived)中。

### 创建密钥 {#key-create}

`POST /access-keys` 创建一个密钥。

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。3 到 32 个字母、数字、`-` 或 `_`。它必须不同于每个活动密钥的名称，忽略大小写，并把 `-` 和 `_` 视为相同。已归档密钥的名称可以重复使用。 |
| `pass_through` | boolean | 默认 `false`。 |
| `confirm_pass_through` | boolean | 当 `pass_through` 为 `true` 时必须为 `true`。 |
| `use_assist_exposure` | boolean | 默认 `false`。仅在 `pass_through` 为 `true` 时保留。 |
| `expires_at` | string | 可选。未来的 ISO 8601 时间。没有时区时，Phoenix 按 Home Assistant 的本地时间读取，并以 UTC 存储。省略该字段表示无有效期；`null` 会被拒绝。 |
| `rate_limit_requests` | integer | 0 到 100000（默认 60）。设为 0 可关闭速率限制。 |
| `rate_limit_burst` | integer | 0 到 100000（默认 10）。当 `rate_limit_requests` 为 0 时存为 0。 |

创建只读取这些字段。新密钥从角色 `custom`、每项能力 `deny`、空权限树、`confirm_inline_wait_seconds` 为 0、`announce_all_tools` 关闭开始。之后用 [PATCH](guide:#key-change) 设置其余内容。

响应为 `201`，包含新记录以及 `access_key`，即密钥本身：`phx_` 后跟 64 个字符。Phoenix 仅在此响应中显示它，请立即保存。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nameRequired` | 缺少 `name`。 |
| 400 | `invalid_request` | `adminError.accessKeyNameLengthPattern` | `name` 违反命名规则。`message_params` 包含 `min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | `pass_through` 或 `use_assist_exposure` 不是布尔值。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.passThroughConfirmationRequired` | `pass_through` 为 `true`，但没有 `confirm_pass_through: true`。 |
| 400 | `invalid_request` | `adminError.badExpiresAt` | `expires_at` 不是可用的时间。 |
| 400 | `invalid_request` | `adminError.expiresInPast` | `expires_at` 不在未来。 |
| 400 | `invalid_request` | `adminError.rateLimitsInteger` | 速率限制不是整数。 |
| 400 | `invalid_request` | `adminError.rateLimitsNonNegative` | 速率限制为负数。 |
| 400 | `invalid_request` | `adminError.rateLimitsMaximum` | 速率限制超过 `max`（100000）。 |
| 409 | `conflict` | `adminError.accessKeyNameTaken` | 已有活动密钥使用此名称。 |

### 读取密钥 {#key-read}

`GET /access-keys/{id}` 返回密钥的记录。

### 更改密钥 {#key-change}

`PATCH /access-keys/{id}` 更改这些字段的任意子集。响应为 `200`，包含更新后的记录。

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 与创建时相同的规则，并与每个其他活动密钥比较。 |
| `pass_through` | boolean | 对已关闭该选项的密钥开启它，还需要 `confirm_pass_through: true`。 |
| `confirm_pass_through` | boolean | 参见 `pass_through`。 |
| `use_assist_exposure` | boolean | 仅当密钥最终开启 `pass_through` 时允许。 |
| `announce_all_tools` | boolean | |
| `confirm_inline_wait_seconds` | integer | 0（关闭）或 30 到 180。 |
| `rate_limit_requests` | integer | 0 到 100000。设为 0 也会把 `rate_limit_burst` 设为 0。 |
| `rate_limit_burst` | integer | 0 到 100000。 |
| `persona` | string | 以下之一：`home_companion`、`read_only`、`voice_assistant`、`automation_builder`、`power_user`、`home_admin`、`new_user`、`dashboard_designer`、`maintenance`、`esphome`、`custom`。 |
| `notes` | string 或 null | 最多 2000 个字符，在行尾变为 LF 并去除首尾空格之后。控制字符、双向覆盖字符和标签字符会被拒绝。`null` 或 `""` 会清除备注。 |
| `cap_*` | string | 任意[能力字段](guide:#capability-fields)：`deny`、`allow` 或 `confirm`。 |

PATCH 拒绝不在此表中的任何字段，且 `expires_at` 根本无法更改。

`persona` 设置密钥的角色标签。它不会更改任何能力，因此请在同一请求中发送你想要的能力值。

角色为 `home_companion` 的密钥始终关闭 `pass_through` 和 `announce_all_tools`。要求开启其中任一都会被拒绝。

重命名密钥会更改其传感器和设备在 Home Assistant 中的显示名称。它们的实体 ID 保持不变。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.expiresImmutable` | 正文包含 `expires_at`。Phoenix 会在查找密钥之前检查这一点。 |
| 400 | `invalid_request` | `adminError.unknownFields` | 正文包含表中没有的字段。`message_params` 以 `fields` 列出它们。 |
| 400 | `invalid_request` | `adminError.nameRequired` | `name` 为空。 |
| 400 | `invalid_request` | `adminError.accessKeyNameLengthPattern` | `name` 违反命名规则。`message_params` 给出 `min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.accessKeyNameTaken` | 另一个活动密钥已使用此名称。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | 某个布尔字段使用了其他类型。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.passThroughConfirmationRequired` | `pass_through` 开启但没有 `confirm_pass_through: true`。 |
| 400 | `invalid_request` | `adminError.assistExposurePassThroughOnly` | 没有直通模式却使用了 `use_assist_exposure`。 |
| 400 | `invalid_request` | `adminError.inlineWaitRange` | `confirm_inline_wait_seconds` 超出范围。`message_params` 给出 `min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.integerRequired` | 速率限制不是整数。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.nonNegativeRequired` | 速率限制为负数。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.maximumRequired` | 速率限制超过 100000。`message_params` 给出 `field` 和 `max`。 |
| 400 | `invalid_request` | `adminError.unknownPersona` | `persona` 不是列出的值之一。 |
| 400 | `invalid_request` | `adminError.companionSettingRefused` | Home Companion 密钥要求 `pass_through` 或 `announce_all_tools`。 |
| 400 | `invalid_request` | `adminError.notesString` | `notes` 不是字符串或 `null`。 |
| 400 | `invalid_request` | `adminError.notesTooLong` | `notes` 超过 `max` 个字符。 |
| 400 | `invalid_request` | `adminError.notesInvalidCharacters` | `notes` 包含被拒绝的字符。 |
| 400 | `invalid_request` | `adminError.capabilityModeInvalid` | 某个 `cap_*` 值不是 `deny`、`allow` 或 `confirm`。`message_params` 指明 `capability`。 |
| 400 | `invalid_request` | `adminError.capabilityConfirmUnsupported` | 对不接受 `confirm` 的能力使用了 `confirm`。`message_params` 指明 `capability`。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

### 吊销密钥 {#key-revoke}

`DELETE /access-keys/{id}` 立即吊销密钥并将其移到归档。响应为 `204`，没有正文。Phoenix 还会：

- 从 Assist、语音智能体和 AI 任务绑定中移除密钥。Phoenix 为该密钥创建的 Assist 管道或 AI 任务实体也会被移除。
- 取消该密钥排队的审批，并释放其 MESA 租约。
- 丢弃其速率限制状态和请求计数，并移除其传感器。
- 触发 `phoenix_mcp_access_key_revoked` 事件，包含 `access_key_id`、`access_key_name`、`revoked_by` 和 `timestamp`。

相关：[{{label:accessKeys.revoke}}](guide:window.accessKeys#revoke)

### 轮换密钥 {#key-rotate}

`POST /access-keys/{id}/rotate` 为密钥提供新值，不接受正文。旧值立即失效。密钥保留其速率限制状态和计数。

响应为 `200`，包含记录以及 `access_key`，即新值。与创建时一样，Phoenix 只显示一次。Phoenix 触发 `phoenix_mcp_access_key_rotated` 事件，包含 `access_key_id`、`access_key_name`、`rotated_by` 和 `timestamp`。

相关：[{{label:accessKeys.rotate}}](guide:window.accessKeys#rotate)

### 密钥统计 {#key-stats}

`GET /access-keys/{id}/stats` 返回密钥的请求计数。每次 Home Assistant 重启后，计数器从零开始。

| 字段 | 类型 | 说明 |
|---|---|---|
| `access_key_id` | string | |
| `access_key_name` | string | |
| `request_count` | integer | 自重启以来的请求数。 |
| `denied_count` | integer | 自重启以来被拒绝访问的请求数。 |
| `rate_limit_hits` | integer | 自重启以来因速率限制被拒绝的请求数。 |
| `last_used_at` | string 或 null | |
| `status` | string | `active` 或 `expired`。 |

### 连接检查 {#key-connection}

`GET /access-keys/{id}/connection` 返回 `last_used_at` 和 `request_count`。智能体用有效密钥发送的每个请求都会增加 `request_count`，因此轮询此端点可查看应用是否已连接。

### 密钥审计日志 {#key-audit}

`GET /access-keys/{id}/audit` 返回密钥审计行的裸数组，最新的在前。管理员对密钥所做的更改会以管理员身份记录，因此不会出现在这里。

| 查询参数 | 说明 |
|---|---|
| `limit` | 最多 500（默认 100）。 |
| `offset` | 要跳过的行数。 |
| `outcome` | 以下之一：`allowed`、`denied`、`not_found`、`rate_limited`、`not_implemented`、`invalid_request`、`pending_approval`。 |
| `ip` | 匹配客户端 IP 包含此文本的行。 |

每行有 `request_id`、`timestamp`、`access_key_id`、`access_key_name`、`method`、`resource`、`outcome`、`client_ip` 和 `pass_through`。设置后，行还会包含 `payload`、`mesa_advisory`、`preset`、`stale_tools_advisory`、`ha_user_id` 和 `originating_access_key_id`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination` | `limit` 或 `offset` 不是整数。 |
| 400 | `invalid_request` | `adminError.paginationNonNegative` | `limit` 或 `offset` 为负数。 |
| 400 | `invalid_request` | `adminError.outcomeFilterUnknown` | `outcome` 不是列出的值之一。`message_params` 给出 `value`。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

### 已归档密钥 {#key-archived}

`GET /access-keys/archived` 返回已吊销和已过期密钥的裸数组。每个条目有 `id`、`name`、`created_at`、`created_by`、`revoked_at`、`revoked`、`expires_at` 和 `last_used_at`。已过期的密钥显示 `revoked: false`。

`DELETE /access-keys/archived/{id}` 永久删除已归档记录。响应为 `204`，没有正文。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.archivedAccessKeyNotFound` | 没有已归档记录具有此 ID。 |

## 预设 {#presets}

```
POST   /api/phoenix-mcp/admin/access-keys/{id}/presets
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}
DELETE /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}
POST   /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}/apply
```

预设保存密钥的设置，以便你之后切回。`{pid}` 是预设的 ID。每个预设端点都返回密钥的记录。

预设默认关闭。请先开启 `access_key_presets_enabled` 设置。在此之前，每个预设端点都会在查找密钥之前返回 `403`。

预设保存密钥的设置：`caps`（全部 32 项能力）、`persona`、`permissions`、`pass_through`、`use_assist_exposure` 和 `announce_all_tools`。它还保存 `confirm_inline_wait_seconds`、`rate_limit_requests` 和 `rate_limit_burst`，以及自己的 `id`、`name` 和 `created_at`。密钥的名称和备注不属于其中。

每个密钥最多保存 8 个预设。预设名称去除首尾空格后为 1 到 40 个字符，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;`。同一密钥内的名称必须不同，忽略大小写。

每个预设端点还可能返回：

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 403 | `forbidden` | `adminError.presetsDisabled` | `access_key_presets_enabled` 已关闭。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

### 保存预设 {#preset-create}

`POST /access-keys/{id}/presets` 把密钥的当前设置保存为新预设，并使其成为当前预设。响应为 `201`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 必填。上面的预设名称规则。 |

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nameRequired` | 缺少 `name`。 |
| 400 | `invalid_request` | `adminError.presetNameTooLong` | `name` 超过 `max` 个字符。 |
| 400 | `invalid_request` | `adminError.presetNameInvalidCharacters` | `name` 包含被拒绝的字符。 |
| 400 | `invalid_request` | `adminError.presetNameTaken` | 密钥已有使用此名称的预设。 |
| 400 | `invalid_request` | `adminError.presetLimitReached` | 密钥已有 8 个预设。`message_params` 包含 `max`。 |

### 重命名预设 {#preset-rename}

`PATCH /access-keys/{id}/presets/{pid}` 重命名预设。正文和名称错误与保存时相同。响应为 `200`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetRenameRejected` | 此密钥的另一个预设使用了该名称。 |
| 404 | `not_found` | `adminError.presetNotFound` | 密钥没有此 ID 的预设。 |

### 删除预设 {#preset-delete}

`DELETE /access-keys/{id}/presets/{pid}` 删除非当前预设。它不接受正文，并返回 `200` 和记录，而不是 `204`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetDeleteRejected` | 该预设是当前预设。 |
| 404 | `not_found` | `adminError.presetNotFound` | 密钥没有此 ID 的预设。 |

### 应用预设 {#preset-apply}

`POST /access-keys/{id}/presets/{pid}/apply` 让密钥匹配预设，并将其标记为当前。响应为 `200`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `confirm_pass_through` | boolean | 可选。当预设为已关闭直通模式的密钥开启直通模式时，必须为 `true`。 |

在切换到不同预设之前，Phoenix 会把密钥的当前设置保存到当前预设中。再次应用当前预设，会把密钥恢复为该预设保存的状态。

应用会设置角色、直通模式、Assist 公开范围、工具声明、同步等待审批、速率限制、能力和权限树。它还会重新开始密钥的速率限制计数。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetPassThroughConfirmationRequired` | 预设开启直通模式，但正文缺少 `confirm_pass_through: true`。 |
| 404 | `not_found` | `adminError.presetNotFound` | 密钥没有此 ID 的预设。 |

## 权限 {#permissions}

```
GET    /api/phoenix-mcp/admin/access-keys/{id}/permissions
PUT    /api/phoenix-mcp/admin/access-keys/{id}/permissions
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/domains/{node}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/devices/{node}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/entities/{node}
GET    /api/phoenix-mcp/admin/access-keys/{id}/permissions/integration-options
POST   /api/phoenix-mcp/admin/access-keys/{id}/permissions/bulk-select
```

这些端点读取和设置权限树：密钥可以读取和控制哪些域、设备和实体。每个节点有一个 `state` 和一个可选的 `hint`，供智能体使用。

```json
{
  "domains":  { "light": { "state": "YELLOW", "hint": null } },
  "devices":  { "<device_id>": { "state": "GREEN", "hint": "Desk lamp" } },
  "entities": { "light.desk": { "state": "RED", "hint": null } }
}
```

节点 ID 遵循[路径中的 ID](guide:topic.adminApi#ids)：域在 `domains` 下，设备 ID 在 `devices` 下，实体 ID 在 `entities` 下。状态参见[权限状态](guide:topic.adminApi#states)。

相关：[权限如何判定](guide:topic.permissionResolution)、[{{label:accessKeys.permissionsTree}}](guide:window.permissions)

### 读取权限树 {#permissions-read}

`GET /access-keys/{id}/permissions` 返回权限树。

### 替换权限树 {#permissions-replace}

`PUT /access-keys/{id}/permissions` 用正文替换整个权限树。响应为 `200`，包含新权限树。

| 字段 | 类型 | 说明 |
|---|---|---|
| `domains`、`devices`、`entities` | object | 每个都把节点 ID 映射到节点对象。Phoenix 忽略其他顶层字段。 |
| `state` | string | 在每个节点中。默认为 `GREY`。 |
| `hint` | string 或 null | 在每个节点中。最多 200 个字符，按发送内容原样保留。 |

要清除所有权限，请发送 `{"domains": {}, "devices": {}, "entities": {}}`。

相关：[{{label:workspace.clearAllPermissions}}](guide:window.permissions#clear-all)

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.permissionSectionObject` | 某个 `section` 不是对象。 |
| 400 | `invalid_request` | `adminError.permissionNodeObject` | 某个 `node` 不是对象。 |
| 400 | `invalid_request` | `adminError.nodeIdTooLong` | 某个节点 ID 超过 255 个字符。 |
| 400 | `invalid_request` | `adminError.nodeIdInvalidChars` | 某个节点 ID 包含被拒绝的字符。 |
| 400 | `invalid_request` | `adminError.badDomain` | 某个域 ID 不是有效域。 |
| 400 | `invalid_request` | `adminError.badEntityId` | 某个实体 ID 不是有效实体 ID。 |
| 400 | `invalid_request` | `adminError.permissionStateInvalidDomain`、`adminError.permissionStateInvalidDevice` 或 `adminError.permissionStateInvalidEntity` | 节点的 `state` 不是四种之一。键名指明节点类型；`message_params` 给出 `state`、`node` 和 `states`。 |
| 400 | `invalid_request` | `adminError.permissionHintString` | 节点的 `hint` 不是字符串或 `null`。 |
| 400 | `invalid_request` | `adminError.permissionHintTooLong` | 节点的 `hint` 超过 `max` 个字符。 |
| 400 | `invalid_request` | `adminError.permissionTreeInvalid` | 权限树无法按发送内容保存。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

### 设置一个节点 {#permissions-node}

`PATCH /access-keys/{id}/permissions/domains/{node}`、`.../devices/{node}` 和 `.../entities/{node}` 设置一个节点。`{node}` 是域、设备 ID 或实体 ID。响应为 `200`，包含整个权限树。

| 字段 | 类型 | 说明 |
|---|---|---|
| `state` | string | 必填。四种状态之一。 |
| `hint` | string 或 null | 可选。最多 200 个字符。省略它可保留节点的提示；`null` 或 `""` 会清除它。 |

在没有提示的节点上设置 `GREY` 会移除该节点。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nodeIdTooLong` | `{node}` 超过 255 个字符。Phoenix 会先检查节点 ID。 |
| 400 | `invalid_request` | `adminError.nodeIdInvalidChars` | `{node}` 包含被拒绝的字符。 |
| 400 | `invalid_request` | `adminError.badDomain` | `{node}` 不是有效域。 |
| 400 | `invalid_request` | `adminError.badEntityId` | `{node}` 不是有效实体 ID。 |
| 400 | `invalid_request` | `adminError.stateAllowed` | `state` 缺失或不是四种之一。`message_params` 列出 `states`。 |
| 400 | `invalid_request` | `adminError.hintString` | `hint` 不是字符串或 `null`。 |
| 400 | `invalid_request` | `adminError.hintTooLong` | `hint` 超过 `max` 个字符。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

### 集成选项 {#integration-options}

`GET /access-keys/{id}/permissions/integration-options` 列出可与[批量选择](guide:#bulk-select)一起使用的集成。响应为 `{"integrations": [...]}`，按标题、域和条目 ID 排序。

每个至少拥有一个设备或一个无设备的活动实体的配置条目对应一行：

| 字段 | 类型 | 说明 |
|---|---|---|
| `entry_id` | string | 用作 `selector_id`。 |
| `domain` | string | 集成的域。 |
| `title` | string | 条目的标题。 |
| `device_count` | integer | |
| `deviceless_entity_count` | integer | |
| `device_ids` | array | |
| `entity_ids` | array | 没有设备的活动、已启用实体。 |
| `registry_only_deviceless_count` | integer | 没有设备且没有活动状态的实体。 |
| `required_domain_ids` | array | |
| `shared_device_count` | integer | |

### 批量选择 {#bulk-select}

`POST /access-keys/{id}/permissions/bulk-select` 为区域、标签或集成中的所有内容设置一种状态。

| 字段 | 类型 | 说明 |
|---|---|---|
| `selector_type` | string | `area`、`label` 或 `integration`。 |
| `selector_id` | string | 必填。区域 ID、标签 ID 或配置条目 ID。 |
| `state` | string | 四种状态之一。 |

- 集成会设置其设备节点及其没有设备的实体节点。
- 区域或标签只设置实体节点，适用于区域或标签匹配的每个实体。实体的标签包括其设备的标签。
- 现有提示会保留。使用 `GREY` 时，没有提示的节点会被移除，有提示的节点保持为 `GREY`。

响应包含新权限树和摘要：

```json
{
  "permissions": { "domains": {}, "devices": {}, "entities": {} },
  "summary": {
    "selector_type": "area", "selector_id": "kitchen", "state": "GREEN",
    "device_count": 0, "entity_count": 12,
    "registry_only_deviceless_count": 0, "required_domain_ids": [],
    "shared_device_count": 0
  }
}
```

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.selectorTypeInvalid` | `selector_type` 不是三者之一。 |
| 400 | `invalid_request` | `adminError.selectorIdRequired` | `selector_id` 缺失或为空。 |
| 400 | `invalid_request` | `adminError.stateAllowed` | `state` 不是四种之一。`message_params` 列出 `states`。 |
| 404 | `not_found` | `adminError.permissionSelectionNotFound` | 条目不存在，或没有实体匹配该区域或标签。Phoenix 会在查找密钥之前检查这一点。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

相关：[{{label:selectBy.menu}}](guide:window.permissions#select-by)

## 实体提示 {#entity-hints}

```
GET    /api/phoenix-mcp/admin/entity-hints
PUT    /api/phoenix-mcp/admin/entity-hints/{entity_id}
```

全局提示会告诉每个智能体某个实体的情况，无论使用哪个密钥。密钥自己权限节点上的提示优先于全局提示。

智能体会先看到密钥实体节点上的提示，然后是设备节点，再是域节点，最后才是全局提示。Phoenix 会移除看起来像机密的文本，并把密钥无法访问的实体 ID 替换为 `<redacted-entity>`。

相关：[实体提示](guide:window.permissions#hints)

### 读取全局提示 {#hints-read}

`GET /entity-hints` 返回每个全局提示：

```json
{ "entity_hints": { "light.desk": "Desk lamp by the window" } }
```

### 设置全局提示 {#hints-set}

`PUT /entity-hints/{entity_id}` 设置或清除一个提示。响应为整个 `entity_hints` 映射。Phoenix 只检查实体 ID 的格式，不检查实体是否存在。

| 字段 | 类型 | 说明 |
|---|---|---|
| `hint` | string 或 null | 去除首尾空格后最多 200 个字符。`null`、`""` 或没有 `hint` 字段会清除它。 |

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | `{entity_id}` 不是有效实体 ID。 |
| 400 | `invalid_request` | `adminError.hintString` | `hint` 不是字符串或 `null`。 |
| 400 | `invalid_request` | `adminError.hintTooLong` | `hint` 超过 `max` 个字符。 |

## 实体 {#entities}

```
GET    /api/phoenix-mcp/admin/entities
GET    /api/phoenix-mcp/admin/access-keys/{id}/resolve/{entity_id}
GET    /api/phoenix-mcp/admin/access-keys/{id}/scope
```

这些端点显示权限编辑器所看到的你的家，以及一个密钥实际可以访问什么。

### 实体树 {#entity-tree}

`GET /entities` 返回每个实体，按域和设备分组。它不分页。Phoenix 会缓存该树；添加任何非空值的 `force_reload` 可重建它。

```json
{
  "light": {
    "devices": {
      "<device_id>": {
        "device_id": "...", "parent_device_id": null, "name": "...",
        "area_id": "...", "area_name": "...", "entities": ["light.desk"]
      }
    },
    "deviceless_entities": ["light.porch"],
    "entity_details": {
      "light.desk": {
        "entity_id": "light.desk", "friendly_name": "...", "device_id": "...",
        "area_id": "...", "area_name": "...", "labels": [{"id": "...", "name": "..."}]
      }
    }
  }
}
```

- 已禁用的实体、`phoenix_mcp` 域和 Phoenix 自己的传感器会被排除。
- 有状态但没有注册表条目的实体会出现在 `deviceless_entities` 中。
- 子设备的主设备会出现在子设备所在的每个域中。
- `person`、`tag` 和 `zone` 始终存在，即使为空。
- 实体的 `labels` 包括其设备的标签。

### 解释一项权限 {#resolve}

`GET /access-keys/{id}/resolve/{entity_id}` 显示 Phoenix 如何判定密钥对某个实体的权限。

| 字段 | 类型 | 说明 |
|---|---|---|
| `entity_id` | string | |
| `resolution_path` | array | `{level, state}` 步骤，最外层在前。 |
| `effective` | string | `WRITE`、`READ`、`DENY`、`NO_ACCESS` 或 `NOT_FOUND`。 |
| `effective_hint` | string 或 null | 智能体看到的提示，顺序见[实体提示](guide:#entity-hints)。 |

`level` 的取值依次为 `global`（始终为 `GREY`）、`domain:<domain>`，然后是对实体之上每个设备的 `device:<name>`（主设备在前），最后是 `entity:<entity_id>`。设备层级显示设备名称，而不是其 ID。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | `{entity_id}` 不是有效实体 ID。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 没有活动密钥具有此 ID。 |

相关：[权限如何判定](guide:topic.permissionResolution)

### 密钥范围 {#scope}

`GET /access-keys/{id}/scope` 列出密钥可以读取和控制的实体。

| 字段 | 类型 | 说明 |
|---|---|---|
| `access_key_id` | string | |
| `access_key_name` | string | |
| `readable` | array | 实体 ID，已排序。 |
| `writable` | array | 实体 ID，已排序。 |
| `persona` | string | |
| `capability_flags` | object | 密钥存储的能力值，在直通模式应用之前。 |
