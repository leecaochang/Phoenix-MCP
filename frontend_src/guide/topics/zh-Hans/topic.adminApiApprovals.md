---
{ "id": "topic.adminApiApprovals", "title": "审批与变更端点", "parent": "topic.adminApi", "order": 20, "keywords": ["审批 API", "批准", "拒绝", "取消", "版本", "回滚", "撤销", "恢复", "需要关注", "卡片目录"], "sectionKeywords": { "approvals": ["待处理", "approval_id", "PendingApproval"], "list-approvals": ["in_progress", "状态筛选", "access_key_id"], "approve": ["approvalExecutionInterrupted", "approvalProcessing", "killSwitchEngaged", "approvalCapabilityDenied", "approvalAccessKeyInactive", "execution_failed", "refused_before_write"], "batch-approve": ["approval_ids", "批处理", "applied", "remaining", "全部批准"], "reject": ["原因", "closure_reason"], "cancel": ["DELETE", "admin_cancelled"], "claims": ["phoenix_mcp_approval_claimed", "phoenix_mcp_approval_resolved", "claim", "认领", "事件"], "history": ["版本", "resource_type", "resource_id", "truncated"], "read-version": ["restore_refusals", "yaml_tags", "audit_only", "template_incompatible", "entity_missing"], "restore-version": ["expected_latest_id", "stale_version", "restore_unverified", "rollback", "side"], "attention": ["attention", "需要关注", "未知结果", "checked_outcome", "日历"], "recovery": ["config-recovery", "安全副本", "快照", "phase"], "recovery-action": ["inspect", "reviewed", "delete", "recoveryBlocked"], "recovery-restore": ["isError", "recoveryManualOnly", "zigbee"], "card-catalog": ["自定义卡片", "采集", "resource_fingerprint", "stub_config"] } }
---
这些端点处理智能体请求你批准的事项，以及它们所做的更改：审批、配置历史、恢复记录、需要关注和仪表盘卡片目录。登录、错误正文和请求体如[管理 API](guide:topic.adminApi)所述。

相关：[错误](guide:topic.adminApi#errors)、[请求体](guide:topic.adminApi#bodies)、[审批如何运作](guide:topic.approvalLifecycle)、[变更记录的内容](guide:topic.configurationHistory)

## 审批 {#approvals}

```
GET    /api/phoenix-mcp/admin/approvals
GET    /api/phoenix-mcp/admin/approvals/{approval_id}
DELETE /api/phoenix-mcp/admin/approvals/{approval_id}
POST   /api/phoenix-mcp/admin/approvals/{approval_id}/approve
POST   /api/phoenix-mcp/admin/approvals/batch/approve
POST   /api/phoenix-mcp/admin/approvals/{approval_id}/reject
```

使用它们审核、批准、拒绝或取消智能体正在等待的操作。针对单个审批的端点返回此审批记录：

| 字段 | 说明 |
|---|---|
| `id` | 审批 ID。 |
| `access_key_id`、`access_key_name` | 发起请求的访问密钥。 |
| `tool_name`、`cap_name` | 被暂缓等待审批的工具，以及它需要的能力。 |
| `args` | 工具的参数。机密、设备代码和每个以 `_` 开头的键都显示为 `<redacted>`。 |
| `diff` | 提议的更改。 |
| `status` | `pending`、`approved`、`rejected`、`failed`、`expired` 或 `cancelled`。 |
| `created_at`、`expires_at`、`resolved_at` | 请求时间、过期时间和结束时间。 |
| `approved_by_user_id`、`approved_at` | 谁批准了它，以及何时。 |
| `closure_reason` | 为什么它未运行就结束，或为什么运行失败。取值见下文。 |
| `result` | 批准后工具的结果。 |
| `request_id`、`client_ip` | 发起请求的智能体请求。 |
| `household_request`、`ha_user_id` | 家庭请求标志，以及请求背后的 Home Assistant 用户。 |
| `execution_started_at` | 批准开始运行操作的时间。 |

`closure_reason` 是你自己的拒绝原因，或以下值之一：

```text
access_key_inactive        capability_denied
kill_switch                admin_cancelled
access_key_revoked         access_key_expired
phoenix_mcp_data_wiped     execution_interrupted
execution_failed           agent_chat_ended
agent_chat_wait_timed_out  integration_removed
recovery_review_replaced   phoenix_mcp_restarted
```

相关：[审批](guide:window.approvals)、[它可能如何结束](guide:topic.approvalLifecycle#outcomes)

### 列出审批 {#list-approvals}

列出审批，最新的在前。

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | query, string | 仅列出状态完全为此值的审批。未知值返回空列表。 |
| `access_key_id` | query, string | 仅列出此访问密钥的审批。 |
| `limit` | query, integer | 默认 50，最多 500。更大的值按 500 计。 |
| `offset` | query, integer | 默认 0。 |

响应为 `{ approvals, total, limit, offset }`。`approvals` 中的每一项都是记录加上 `in_progress`，在批准正在运行它时为 `true`。即使其状态仍为 `pending`，也应把这样的行视为不可操作。

错误：[请求体](guide:topic.adminApi#bodies)中的分页错误。

### 读取单个审批 {#read-approval}

返回审批记录，不含 `in_progress`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.approvalNotFound` | 没有审批具有此 ID。 |

### 批准 {#approve}

批准该审批记录，并按智能体的请求运行暂缓的操作。该端点不接受正文，并忽略你发送的任何正文。

Phoenix 按下文错误表的顺序检查审批。运行任何内容之前，它会向磁盘保存一个执行标记。如果某次运行到达操作却从未回报，该标记会保留，之后每次批准、拒绝或取消都会返回 `409`。

MESA 为确认而暂缓的审批，以及恢复审核，会跳过能力检查。恢复审核还会跳过紧急停止开关检查。

响应为 `200`，包含更新后的记录：

- 操作运行时 `status: "approved"`。
- 工具返回错误时 `status: "failed"`，且 `closure_reason: "execution_failed"`。这仍是 `200`。
- 工具已发送更改但无法确认是否生效时 `status: "failed"`，且 `closure_reason: "execution_interrupted"`。重试前请先检查结果。这仍是 `200`。
- `result` 为 `{ tool_result, outcome }`。当工具在写入任何内容之前拒绝时，它会添加 `refused_before_write: true`。
- 已经结束的审批会原样返回。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 409 | `conflict` | `adminError.approvalWipeInProgress` | Phoenix 数据正在被删除。 |
| 404 | `not_found` | `adminError.approvalNotFound` | 没有审批具有此 ID，或它在运行时消失了。 |
| 409 | `conflict` | `adminError.approvalProcessing` | 另一个批准正在运行它。 |
| 409 | `conflict` | `adminError.approvalExecutionInterrupted` | 较早的批准已到达操作，但从未回报。在做其他任何事情之前，请检查结果。 |
| 409 | `conflict` | `adminError.approvalExpired` | 它已超过 `expires_at`，因此本次调用以 `expired` 关闭它。没有运行任何内容。 |
| 409 | `conflict` | `adminError.approvalAlreadyResolved` | 另一个请求先完成了它。 |
| 404 | `not_found` | `adminError.approvalAccessKeyInactive` | 访问密钥不再活动。Phoenix 以 `cancelled` 关闭审批，原因为 `access_key_inactive`。 |
| 403 | `forbidden` | `adminError.approvalCapabilityDenied` | 此密钥的能力现在设为拒绝访问。Phoenix 以 `failed` 关闭审批，原因为 `capability_denied`。 |
| 503 | `service_unavailable` | `adminError.killSwitchEngaged` | 紧急停止开关已开启。Phoenix 以 `cancelled` 关闭审批，原因为 `kill_switch`。 |
| 503 | `service_unavailable` | `adminError.approvalRecordFailed` | Phoenix 无法保存执行标记。没有运行任何内容，审批仍为待处理。 |
| 400 | `invalid_request` | `adminError.approvalExecutorMissing` | Phoenix 没有运行此工具的方式。没有运行任何内容。 |
| 500 | `internal_error` | `adminError.approvalExecutionUncertain` | 运行引发错误。Phoenix 以 `failed` 关闭审批，原因为 `execution_interrupted`，并且不会再提供它。 |

其中三个错误听起来是临时的，但会永久关闭审批：`approvalAccessKeyInactive`、`approvalCapabilityDenied` 和 `killSwitchEngaged`。之后修复条件也不会让它恢复。

### 批准多个 {#batch-approve}

按顺序逐个批准多个审批，经过与单个批准相同的步骤。批处理会在第一个未返回 `200` 且 `status: "approved"` 的审批处停止。

| 字段 | 类型 | 说明 |
|---|---|---|
| `approval_ids` | 字符串数组 | 必填。1 到 50 个 ID，每个为非空字符串，不能重复。 |

响应始终为 `200`：

```json
{
  "applied": [{ "approval_id": "...", "tool_name": "..." }],
  "failed": {
    "approval_id": "...",
    "tool_name": "...",
    "status": 409,
    "error": "conflict",
    "message": "...",
    "message_key": "adminError.approvalProcessing"
  },
  "remaining": ["..."]
}
```

- `applied` 列出已运行的审批。已经批准的审批也算作已应用。
- 当每个审批都运行时，`failed` 为 `null`。其 `status` 是该项目的 HTTP 状态。其 `error` 是该项目的错误代码，否则是其 `closure_reason`，否则为 `"failed"`。
- 当项目的错误带有 `message_key` 和 `message_params` 时，`failed` 也会携带它们。
- `remaining` 列出批处理未尝试的 ID。

批处理会添加一条审计日志条目，而不是每个审批一条。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.approvalIdsNonEmpty` | `approval_ids` 缺失、为空，或包含非空字符串以外的内容。 |
| 400 | `invalid_request` | `adminError.approvalIdsUnique` | 某个 ID 重复。 |
| 400 | `invalid_request` | `adminError.approvalBatchMaximum` | 超过 50 个 ID。`message_params.max` 为 50。 |

### 拒绝 {#reject}

拒绝待处理的审批，使操作永不运行。

| 字段 | 类型 | 说明 |
|---|---|---|
| `reason` | string | 可选。存储在 `closure_reason` 中。 |

响应为更新后的记录，`status: "rejected"`。已经结束的审批会以 `200` 原样返回。超过 `expires_at` 的待处理审批会改为以 `expired` 关闭。

拒绝恢复审核还会记录你已检查它。如果恢复记录拒绝这一点，你会收到 `409` `conflict`，并带有[对记录执行操作](guide:#recovery-action)中的某个键。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.approvalReasonString` | `reason` 不是字符串。 |
| 404 | `not_found` | `adminError.approvalNotFound` | 没有审批具有此 ID。 |
| 409 | `conflict` | `adminError.approvalProcessing` | 批准正在运行它。 |
| 409 | `conflict` | `adminError.approvalExecutionInterrupted` | 较早的批准已到达操作，但从未回报。请先检查结果。 |

### 取消 {#cancel}

取消待处理的审批。它的工作方式与拒绝相同，`status: "cancelled"`，`closure_reason: "admin_cancelled"`。不发送正文。

响应以及已结束和逾期审批的处理与[拒绝](guide:#reject)相同。错误相同，但 `approvalReasonString` 除外。

### 认领和事件 {#claims}

Phoenix 在 Home Assistant 的事件总线上触发两个事件，以便脚本无需轮询即可跟踪审批：

- 当批准取得审批时，在操作运行前触发 `phoenix_mcp_approval_claimed`，`claimed: true`。数据：`approval_id`、`claimed`、`timestamp`。
- 如果运行前的失败使审批保持待处理，它会再次以 `claimed: false` 触发。
- 审批达到最终状态时触发 `phoenix_mcp_approval_resolved`。数据：`approval_id`、`access_key_id`、`access_key_name`、`tool_name`、`status`、`closure_reason`、`approved_by_user_id`、`timestamp`。

运行期间加载的页面会在[列出审批](guide:#list-approvals)的行上看到 `in_progress: true`。

相关：[自动化事件](guide:topic.running#events)

## 配置历史 {#history}

```
GET    /api/phoenix-mcp/admin/versions
GET    /api/phoenix-mcp/admin/versions/{version_id}
POST   /api/phoenix-mcp/admin/versions/{version_id}/restore
```

使用它们读取智能体和还原所做的更改，并把更改的其中一侧放回去。

Phoenix 记录这些资源类型的版本：

```text
automation  script  scene      helper     dashboard  entity  device
blueprint   energy  config_entry  area    floor      label   category
```

每个版本都有一个操作：`create`、`edit`、`delete` 或 `rollback`。

Phoenix 保留每个资源最新的 20 个版本。超过 100,000 字节的原始文本会存为 `{ "content": null, "truncated": true, "bytes": <size> }`，且无法还原。

相关：[变更](guide:window.changes)、[变更记录的内容](guide:topic.configurationHistory)

### 列出配置版本 {#list-versions}

列出所有资源的近期更改，或一个资源的所有版本。

| 字段 | 类型 | 说明 |
|---|---|---|
| `resource_type` | query, string | 与 `resource_id` 一起使用时，列出该资源存储的每个版本，最新的在前，不分页。 |
| `resource_id` | query, string | 与 `resource_type` 一起发送，或两者都省略以获取近期列表。 |
| `limit` | query, integer | 仅用于近期列表。默认 50，最多 500。更大的值按 500 计。 |
| `offset` | query, integer | 仅用于近期列表。默认 0。 |

响应为 `{ resource_type, resource_id, versions, total }`。`versions` 中的每一项都是摘要：

| 字段 | 说明 |
|---|---|
| `id` | 版本 ID。 |
| `resource_type`、`resource_id`、`alias` | 更改了什么，以及其名称。 |
| `action` | `create`、`edit`、`delete` 或 `rollback`。 |
| `access_key_id`、`access_key_name` | 做出更改的访问密钥。 |
| `approved_by_user_id` | 批准更改或运行还原的管理员。 |
| `timestamp` | 更改时间。 |
| `summary`、`summary_key`、`summary_params` | 简短描述，以及其翻译键和值。 |
| `has_before`、`has_after` | 版本是否包含更改前或更改后的一侧。 |

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.versionResourcePairRequired` | `resource_type` 和 `resource_id` 只发送了一个。 |

[请求体](guide:topic.adminApi#bodies)中的分页错误也适用。

### 读取单个版本 {#read-version}

完整返回一个版本：摘要字段（不含 `has_before` 和 `has_after`），加上 `before`、`after` 和 `request_id`。

它会添加 `restore_refusals: { before, after }`。每一侧为 `null`，或 Phoenix 拒绝还原该侧的原因：`yaml_tags`、`audit_only`、`template_incompatible` 或 `entity_missing`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.versionNotFound` | 没有版本具有此 ID。 |

### 还原一个版本 {#restore-version}

把一个版本的其中一侧应用到当前配置。空正文也可以。

| 字段 | 类型 | 说明 |
|---|---|---|
| `side` | string | 可选。`"before"` 或 `"after"`。默认 `"after"`，当版本没有更改后一侧时默认 `"before"`。 |
| `expected_latest_id` | string 或 null | 可选。设置后，除非这是资源最新版本的 ID，否则还原不会应用任何内容。 |

发送 `expected_latest_id` 可确保你上次读取历史后没有其他更改到达。如果有，你会收到 `stale_version`；请再次读取历史并决定。

还原以你的管理员权限运行。Phoenix 会把它记录为一个 `rollback` 版本，`approved_by_user_id` 为你。审计日志条目会以 `originating_access_key_id` 保留做出原始更改的访问密钥。

响应为 `200`，包含 `{ "restored": true, version_id, resource_type, resource_id }`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.versionNotFound` | 没有版本具有此 ID。 |
| 400 | `invalid_request` | `adminError.restoreSideInvalid` | `side` 不是 `"before"` 或 `"after"`。 |
| 400 | `invalid_request` | `adminError.stringOrNull` | `expected_latest_id` 不是字符串或 null。`message_params.field` 为 `expected_latest_id`。 |
| 409 | `stale_version` | `adminError.restoreStale` | `expected_latest_id` 不再是最新版本。没有应用任何内容。 |
| 500 | `internal_error` | `adminError.restoreFailed` | 还原引发错误。 |
| 409 | `restore_unverified` | `adminError.restoreUnverified` | Phoenix 发送了还原，但读回的资源不匹配。它可能已部分应用，因此重试前请检查。 |
| 400 | `invalid_request` | `adminError.restoreNoConfigSide` | 该侧没有可还原的配置。 |
| 400 | `invalid_request` | `adminError.restoreYamlTags` | 版本包含 `!secret` 或 `!include` 等 YAML 标签。请手工重新创建。 |
| 400 | `invalid_request` | 某个恢复键 | 恢复记录拒绝还原。相关键列在[对记录执行操作](guide:#recovery-action)中。 |
| 400 | `invalid_request` | `adminError.restoreToolReported` | 还原工具因其他原因拒绝。`message_params.detail` 保存其自己的英文文本。 |
| 400 | `invalid_request` | `adminError.restoreNotApplicable` | 还原无法应用到当前配置。 |

## 需要关注 {#attention}

```
GET    /api/phoenix-mcp/admin/attention
POST   /api/phoenix-mcp/admin/attention/{kind}/{record_id}
```

使用它们查找 Phoenix 不知道结果的智能体和日历操作，并在你查看后把它们标记为已检查。

相关：[需要关注](guide:window.attention)

### 列出项目 {#attention-items}

响应为 `{ items }`。每个项目是以下之一：

- 结果未知的智能体操作：`{ id, "kind": "agent", tool, access_key_name, started_at, dispatch_result }`。
- 结果未知的日历写入：`{ id, "kind": "calendar", tool, access_key_name, started_at, entity_id, "dispatch_result": "unknown" }`。其 `tool` 为 `<operation>_calendar_event`。
- 当 Phoenix 无法读取其日历记录时：`{ "id": "calendar_storage", "kind": "calendar_storage" }`。
- 当 Phoenix 上一次保存审计日志失败时：`{ "id": "audit_storage", "kind": "audit_storage" }`。
- 当 MESA 启动失败时：`{ "id": "mesa", "kind": "mesa" }`。

请求写入日历时，其日历项目会替换同一请求的智能体项目。

### 标记操作已检查 {#attention-review}

记录你已检查某个智能体或日历操作的结果。在路径中使用项目的 `kind` 和 `id`。Phoenix 只保存你的审核；它不会再次运行或撤销该操作。

| 字段 | 类型 | 说明 |
|---|---|---|
| `checked_outcome` | boolean | 必填。必须完全为 `true`。 |

Phoenix 会再次从 Home Assistant 读取你的用户，你仍必须是活动管理员。响应为 `{ "reviewed": true }`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.attentionCheckRequired` | `checked_outcome` 不为 `true`。 |
| 403 | `forbidden` | `adminError.adminRequired` | 你不再是活动管理员。 |
| 404 | `not_found` | `adminError.attentionNotReviewable` | `kind` 不是 `agent` 或 `calendar`。 |
| 503 | `service_unavailable` | `adminError.attentionSaveFailed` | Phoenix 无法保存审核。该操作仍需审核。 |
| 409 | `conflict` | `adminError.attentionReviewConflict` | 操作仍在运行，或已有人审核过它。请重新加载列表。 |

## 配置恢复 {#recovery}

```
GET    /api/phoenix-mcp/admin/config-recovery
POST   /api/phoenix-mcp/admin/config-recovery/{recovery_id}
```

使用它们读取 Phoenix 在配置写入前保留的安全副本，并检查、清除或还原它们。

相关：[安全副本](guide:topic.configurationHistory#recovery)

### 列出恢复记录 {#recovery-records}

返回 `{ records }`，最新的在前，不分页。每条记录有这些字段：

| 字段 | 说明 |
|---|---|
| `id` | 恢复 ID。 |
| `resource`、`operation` | 写入更改了什么，以及如何更改。 |
| `access_key_id` | 做出写入的访问密钥。 |
| `created_at` | Phoenix 取得副本的时间。 |
| `phase` | `prepared`、`dispatching`、`partial`、`unknown`、`verified`、`not_applied` 或 `reviewed`。 |
| `before`、`source`、`expected` | 存储的副本，以及写入本来应产生的内容。 |
| `observations` | 阶段变化后 Phoenix 观察到的内容。 |
| `reviewed_by` | 有人审核后，审核者。 |
| `active` | 写入仍在运行时为 `true`。 |

Phoenix 始终保留活动记录和处于 `unknown` 的记录。其余记录保留 30 天，每个资源最多 20 条，总共 256 条。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 503 | `service_unavailable` | `adminError.recoveryStorageUnavailable` | Phoenix 无法加载其恢复记录。 |

### 对记录执行操作 {#recovery-action}

通过 `action` 选择检查、审核、删除或还原一条记录。

| 字段 | 类型 | 说明 |
|---|---|---|
| `action` | string | 必填。`inspect`、`reviewed`、`delete` 或 `restore`。 |
| `side` | string | 仅用于 `restore`。`"before"` 或 `"source"`。 |

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.recoveryNotFound` | 没有记录具有此 ID。 |
| 400 | `invalid_request` | `adminError.recoveryUnknownAction` | `action` 不是四种之一。 |
| 400 | `invalid_request` | `adminError.recoverySideRequired` | `side` 缺失，或该侧没有存储的副本。 |
| 400 | `invalid_request` | `adminError.recoveryManualOnly` | Phoenix 无法还原此类副本。请手工还原。 |
| 409 | `conflict` | 某个恢复键 | 记录拒绝该操作。键名说明原因。 |

恢复键为 `adminError.recoveryStorageUnavailable`、`adminError.recoveryOperationActive`、`adminError.recoveryCommandRunning`、`adminError.recoveryResolveInApprovals`、`adminError.recoveryFull`、`adminError.recoveryReviewBeforeRemoving`、`adminError.recoveryReviewBeforeRestoring`、`adminError.recoveryChangedAfterReview`、`adminError.recoveryAuthorityChanged`、`adminError.recoveryHelperUnreadable`、`adminError.recoverySnapshotUnsafe` 和 `adminError.recoverySnapshotUnsafeAt`。任何其他拒绝使用 `adminError.recoveryBlocked`。`adminError.recoverySnapshotUnsafeAt` 在 `message_params.location` 中给出被拒绝的位置。

### 检查 {#recovery-inspect}

读取记录所覆盖内容的当前状态。它绝不证明是哪个请求造成了该状态。响应取决于副本类型：

- 重启回执：`{ runtime_replaced, "execution_attribution": "unproven" }`。
- 存储辅助元素：`{ observed, helper_present, "execution_attribution": "unproven" }`。
- 模板辅助元素：`{ observed, entry_present, "execution_attribution": "unproven" }`。
- 其他任何类型：`{ "status": "manual_verification_required" }`。

### 审核和删除 {#recovery-review}

`reviewed` 把记录标记为已审核，并返回 `{ "status": "reviewed" }`。`delete` 删除它，并返回 `{ "status": "delete" }`。删除要求阶段为 `verified`、`not_applied` 或 `reviewed`。

写入仍在运行，或 Phoenix 发送的某个 Home Assistant 命令尚未完成时，两者都会被拒绝。

### 还原 {#recovery-restore}

把所选的一侧写回。记录必须处于非活动状态，阶段为 `verified`、`not_applied` 或 `reviewed`，并且该侧有存储的副本。

- Phoenix 会还原存储辅助元素、模板辅助元素、能源偏好、蓝图源，以及仪表盘注册表、布局和资源副本。
- 配置文件文本的副本始终会被拒绝，因为 Phoenix 不会把文件文本写回。
- 其他所有类型，包括重启回执和 Zigbee 副本，都会返回 `400` 和 `adminError.recoveryManualOnly`。

还原返回 `200`，包含 `{ result, outcome }`，其中 `result` 是还原工具的结果。被拒绝或失败的还原也会返回 `200`，`result.isError: true`，并带有工具自己的文本。请检查 `result`，而不是状态代码。

## 仪表盘卡片目录 {#card-catalog}

```
GET    /api/phoenix-mcp/admin/card_catalog
POST   /api/phoenix-mcp/admin/card_catalog
```

卡片目录列出浏览器发现的自定义仪表盘卡片。面板会发送报告；你很少需要这样做。

### 读取目录 {#read-catalog}

响应为 `{ harvested, entries, harvested_at, resource_count, failed_imports, resource_fingerprint }`。在浏览器报告之前，`harvested` 会保持 `false`。这不等于没有自定义卡片。

`entries` 中的每一项有 `type`、`name`、`description`、`documentation_url`、`preview`、`available`、`has_visual_editor`、`stub_config` 和 `source`。`source` 为 `picker` 或 `element`。

### 报告采集结果 {#report-catalog}

用浏览器发现的内容替换整个目录。

| 字段 | 类型 | 说明 |
|---|---|---|
| `entries` | array | 必填。最多保留 500 个条目，每个 `type` 一个。`type` 最多 120 个字符。超过 2000 字节的 `stub_config` 会被丢弃。 |
| `resource_count` | number | 可选。浏览器看到的仪表盘资源数量。 |
| `failed_imports` | `{ url, error }` 的数组 | 可选。最多保留 50 个。 |

Phoenix 会清理每个字段，并跳过错误条目，而不是失败。它会自行读取仪表盘资源，并存储 `resource_fingerprint`，即对其类型和 URL 的 SHA-256。当 Phoenix 无法读取它们时，指纹为 `null`。

响应为 `{ harvested, harvested_at, cards, available, failed_imports }`。最后三个是计数。

面板每个浏览器和面板版本最多每 10 分钟报告一次。当没有发现资源也没有发现卡片时，它会跳过报告，并且只在出现更多卡片时再次报告。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.entriesListRequired` | `entries` 缺失或不是数组。 |
