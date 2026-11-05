---
{ "id": "topic.running", "title": "运行 Phoenix", "parent": "topic.about", "order": 50, "keywords": ["运维", "日常", "监控", "自动化"], "sectionKeywords": { "sensors": ["传感器", "遥测", "Request Count", "Denied Count", "Rate Limit Hits", "Last Access", "Expires In", "状态", "实体 ID", "设备", "统计"], "events": ["事件", "自动化触发器", "phoenix_mcp_access_key_revoked", "phoenix_mcp_access_key_rotated", "phoenix_mcp_access_key_expired", "phoenix_mcp_rate_limited", "事件数据"], "approval-events": ["phoenix_mcp_approval_requested", "phoenix_mcp_approval_resolved", "phoenix_mcp_approval_claimed", "phoenix_mcp_config_changed", "手机通知", "移动应用", "通知审批"], "mesa-events": ["phoenix_mcp_mesa_blocked", "phoenix_mcp_mesa_lease_expired"], "notifications": ["notification_id", "持久通知", "消除"], "storage": [".storage", "文件", "备份", "phoenix_mcp_audit", "phoenix_mcp_conversations"] } }
---
Phoenix 会向 Home Assistant 添加一些内容，帮助你观察它并围绕它实现自动化。每个访问密钥都会获得传感器，Phoenix 会触发事件并显示通知。本页还列出 Phoenix 保存数据的位置。

## 每个密钥的传感器 {#sensors}

每个活动访问密钥都会在 Home Assistant 中显示为一个名为 "Phoenix MCP Access key" 后跟密钥名称的设备。它有六个诊断传感器：

- **Status**：有效、已过期或已吊销。
- **Request Count**：密钥执行的操作数，来自任何应用或 Phoenix 界面。
- **Denied Count**：被权限拒绝，或针对不存在实体的操作数。
- **Rate Limit Hits**：因速率限制被拒绝的请求数。
- **Last Access**：来自外部应用的最后一个请求，精确到分钟。Agent Chat、语音、AI 任务和 Assist 不会改变它。
- **Expires In**：密钥过期前的天数，永不过期时为未知。

每个传感器的实体 ID 是 `sensor.phoenix_mcp_access_key_`，然后是密钥名称和传感器名称。对于名为 `my_access_key` 的密钥，在英语环境下：

```text
sensor.phoenix_mcp_access_key_my_access_key_status
sensor.phoenix_mcp_access_key_my_access_key_request_count
sensor.phoenix_mcp_access_key_my_access_key_denied_count
sensor.phoenix_mcp_access_key_my_access_key_rate_limit_hits
sensor.phoenix_mcp_access_key_my_access_key_last_access
sensor.phoenix_mcp_access_key_my_access_key_expires_in
```

这三个计数保存在内存中，因此 Home Assistant 重启或 Phoenix 重新加载时它们会从 0 重新开始。关闭日志记录时它们仍会继续计数。

重命名密钥会重命名其设备，但保留实体 ID。吊销密钥、密钥过期或删除核心数据会移除设备、其传感器及其统计。

智能体永远无法读取这些传感器，即使使用直通模式。

## 自动化事件 {#events}

Phoenix 会在 Home Assistant 的事件总线上触发这些事件。把它们用作自动化触发器，例如在密钥过期时发送消息。所有时间戳均为 UTC。

- `phoenix_mcp_access_key_revoked`：你吊销了密钥。数据：`access_key_id`、`access_key_name`、`revoked_by`、`timestamp`。
- `phoenix_mcp_access_key_rotated`：你轮换了密钥。数据：`access_key_id`、`access_key_name`、`rotated_by`、`timestamp`。
- `phoenix_mcp_access_key_expired`：密钥超过其有效期并被归档。数据：`access_key_id`、`access_key_name`、`timestamp`。
- `phoenix_mcp_rate_limited`：密钥达到其速率限制，每个密钥每分钟最多一次。数据：`access_key_id`、`access_key_name`、`timestamp`。

删除核心数据或移除集成不会为其移除的密钥触发已吊销或已过期事件。

### 审批和更改 {#approval-events}

- `phoenix_mcp_approval_requested`：有请求正在等待审批。数据：`approval_id`、`access_key_id`、`access_key_name`、`tool_name`、`cap_name`、`review_url`、`expires_at`、`timestamp`。
- `phoenix_mcp_approval_resolved`：请求达到最终状态。数据：`approval_id`、`access_key_id`、`access_key_name`、`tool_name`、`status`、`rejected_reason`、`approved_by_user_id`、`timestamp`。
- `phoenix_mcp_approval_claimed`：已批准的请求开始运行（`claimed` 为 true），或在启动失败后被释放（`claimed` 为 false）。数据：`approval_id`、`claimed`、`timestamp`。
- `phoenix_mcp_config_changed`：变更中记录了一次更改。数据：`resource_type`、`resource_id`、`action`。

`status` 为 approved 表示你批准了它，而不是操作成功。安全副本审核使用访问密钥 ID `__configuration_recovery__`。

要获得打开请求的手机通知，请使用如下自动化，把其中 `notify.mobile_app_your_device` 替换为你手机的 notify 操作。条件会确保链接确实指向此请求的审核页面。

```yaml
alias: Phoenix approval notifications
triggers:
  - trigger: event
    event_type: phoenix_mcp_approval_requested
conditions:
  - condition: template
    value_template: >-
      {% set path = trigger.event.data.get('review_url', '') %}
      {{ path is string and
         path | regex_match('^/phoenix-mcp/approvals/appr_[0-9a-f]{16}$') and
         path == '/phoenix-mcp/approvals/' ~ trigger.event.data.get('approval_id', '') }}
actions:
  - action: notify.mobile_app_your_device
    data:
      title: Phoenix MCP approval
      message: Review the pending request in Phoenix MCP.
      data:
        tag: "phoenix_approval_{{ trigger.event.data.approval_id }}"
        url: "{{ trigger.event.data.review_url }}"
        clickAction: "{{ trigger.event.data.review_url }}"
mode: queued
```

相关：[审批如何运作](guide:topic.approvalLifecycle)、[安全副本](guide:topic.configurationHistory#recovery)

### MESA {#mesa-events}

- `phoenix_mcp_mesa_blocked`：MESA 拒绝了某个实体，每个实体一次。数据：`access_key_id`、`access_key_name`、`entity_id`、`rule_applied`、`timestamp`。
- `phoenix_mcp_mesa_lease_expired`：MESA 租约结束。数据：`lease_id`、`entities`、`reason`、`timestamp`。原因为 `natural_expiry`、`early_release` 或 `session_terminated`。

## 通知 {#notifications}

Phoenix 可以显示 Home Assistant 通知，自动化也可以消除它们：

- [{{label:settings.notifyRateLimitLabel}}](guide:window.settingsLogs#notify-rate-limit)使用 ID `phoenix_mcp_rate_limit_` 后跟密钥的 ID。
- [{{label:settings.notifyApprovalLabel}}](guide:window.settingsLogs#notify-approval)使用 ID `phoenix_mcp_approval_` 后跟审批的 ID。请求解决后 Phoenix 会消除它。

## Phoenix 把数据保存在哪里 {#storage}

Phoenix 把数据保存在 Home Assistant 的 `.storage` 文件夹中，文件名以 `phoenix_mcp` 开头。它们保存访问密钥和设置、审计日志、对话日志、变更历史、安全副本、MESA 策略、AI 提供商账户和路由统计。

Home Assistant 备份会包含它们，因此请把备份视为机密。

相关：[什么存储在哪里](guide:topic.security#stored)、[完全移除 Phoenix](guide:topic.security#removing)
