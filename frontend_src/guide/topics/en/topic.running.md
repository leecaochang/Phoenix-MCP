---
{ "id": "topic.running", "title": "Running Phoenix", "parent": "topic.about", "order": 50, "keywords": ["operations", "day to day", "monitor", "automate"], "sectionKeywords": { "sensors": ["sensor", "telemetry", "Request Count", "Denied Count", "Rate Limit Hits", "Last Access", "Expires In", "Status", "entity ID", "device", "statistics"], "events": ["event", "automation trigger", "phoenix_mcp_access_key_revoked", "phoenix_mcp_access_key_rotated", "phoenix_mcp_access_key_expired", "phoenix_mcp_rate_limited", "event data"], "approval-events": ["phoenix_mcp_approval_requested", "phoenix_mcp_approval_resolved", "phoenix_mcp_approval_claimed", "phoenix_mcp_config_changed", "phone notification", "mobile app", "notify approval"], "mesa-events": ["phoenix_mcp_mesa_blocked", "phoenix_mcp_mesa_lease_expired"], "notifications": ["notification_id", "persistent notification", "dismiss"], "storage": [".storage", "files", "backup", "phoenix_mcp_audit", "phoenix_mcp_conversations"] }, "covers": ["custom_components/phoenix_mcp/sensor.py", "custom_components/phoenix_mcp/approvals.py", "custom_components/phoenix_mcp/helpers.py", "custom_components/phoenix_mcp/admin/settings.py", "custom_components/phoenix_mcp/mesa.py", "custom_components/phoenix_mcp/tool_common.py", "custom_components/phoenix_mcp/const.py"] }
---
Phoenix adds a few things to Home Assistant to help you watch it and automate around it. Each access key gets sensors, and Phoenix fires events and shows notifications. This page also lists where Phoenix keeps its data.

## Sensors for each key {#sensors}

Each active access key appears in Home Assistant as a device named "Phoenix MCP Access key" followed by the key's name. It has six diagnostic sensors:

- **Status**: active, expired or revoked.
- **Request Count**: actions the key has made, from any app or Phoenix surface.
- **Denied Count**: actions refused by permissions, or aimed at entities that don't exist.
- **Rate Limit Hits**: requests refused for the rate limit.
- **Last Access**: the last request from an external app, to the minute. Agent Chat, Voice, AI Task and Assist don't change it.
- **Expires In**: days until the key expires, or unknown if it never does.

Each sensor's entity ID is `sensor.phoenix_mcp_access_key_`, then the key's name and the sensor's. For a key named `my_access_key`, in English:

```text
sensor.phoenix_mcp_access_key_my_access_key_status
sensor.phoenix_mcp_access_key_my_access_key_request_count
sensor.phoenix_mcp_access_key_my_access_key_denied_count
sensor.phoenix_mcp_access_key_my_access_key_rate_limit_hits
sensor.phoenix_mcp_access_key_my_access_key_last_access
sensor.phoenix_mcp_access_key_my_access_key_expires_in
```

The three counts are kept in memory, so they start again from 0 when Home Assistant restarts or Phoenix reloads. They keep counting when logging is off.

Renaming a key renames its device but keeps the entity IDs. Revoking a key, its expiry or deleting core data removes the device, its sensors and their statistics.

Agents can never read these sensors, even with pass-through.

## Events for automations {#events}

Phoenix fires these events on Home Assistant's event bus. Use them as automation triggers, for example to send a message when a key expires. Every timestamp is in UTC.

- `phoenix_mcp_access_key_revoked`: you revoked a key. Data: `access_key_id`, `access_key_name`, `revoked_by`, `timestamp`.
- `phoenix_mcp_access_key_rotated`: you rotated a key. Data: `access_key_id`, `access_key_name`, `rotated_by`, `timestamp`.
- `phoenix_mcp_access_key_expired`: a key passed its expiry and was archived. Data: `access_key_id`, `access_key_name`, `timestamp`.
- `phoenix_mcp_rate_limited`: a key hit its rate limit, at most once a minute per key. Data: `access_key_id`, `access_key_name`, `timestamp`.

Deleting core data or removing the integration doesn't fire revoked or expired events for the keys it removes.

### Approvals and changes {#approval-events}

- `phoenix_mcp_approval_requested`: a request is waiting for approval. Data: `approval_id`, `access_key_id`, `access_key_name`, `tool_name`, `cap_name`, `review_url`, `expires_at`, `timestamp`.
- `phoenix_mcp_approval_resolved`: a request reached its final status. Data: `approval_id`, `access_key_id`, `access_key_name`, `tool_name`, `status`, `closure_reason`, `approved_by_user_id`, `timestamp`.
- `phoenix_mcp_approval_claimed`: an approved request started running (`claimed` true), or was released after a failed start (`claimed` false). Data: `approval_id`, `claimed`, `timestamp`.
- `phoenix_mcp_config_changed`: a change was recorded in Changes. Data: `resource_type`, `resource_id`, `action`.

A `status` of approved means you approved it, not that the action worked. A safety-copy review uses the access key ID `__configuration_recovery__`.

To get a phone notification that opens the request, use an automation like this one, with your phone's notify action in place of `notify.mobile_app_your_device`. The condition makes sure the link really leads to this request's review page.

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

Related: [How an Approval Works](guide:topic.approvalLifecycle), [Safety copies](guide:topic.configurationHistory#recovery)

### MESA {#mesa-events}

- `phoenix_mcp_mesa_blocked`: MESA refused an entity, once per entity. Data: `access_key_id`, `access_key_name`, `entity_id`, `rule_applied`, `timestamp`.
- `phoenix_mcp_mesa_lease_expired`: a MESA lease ended. Data: `lease_id`, `entities`, `reason`, `timestamp`. The reason is `natural_expiry`, `early_release` or `session_terminated`.

## Notifications {#notifications}

Phoenix can show Home Assistant notifications, which an automation can also dismiss:

- [{{label:settings.notifyRateLimitLabel}}](guide:window.settingsLogs#notify-rate-limit) uses the ID `phoenix_mcp_rate_limit_` followed by the key's ID.
- [{{label:settings.notifyApprovalLabel}}](guide:window.settingsLogs#notify-approval) uses the ID `phoenix_mcp_approval_` followed by the approval's ID. Phoenix dismisses it when the request is resolved.

## Where Phoenix keeps its data {#storage}

Phoenix keeps its data in Home Assistant's `.storage` folder, in files whose names start with `phoenix_mcp`. They hold the access keys and settings, the audit log, the Conversation Log, change history, safety copies, MESA profiles, AI provider accounts and routing statistics.

Home Assistant backups include them, so treat backups as secret.

Related: [What's stored where](guide:topic.security#stored), [Removing Phoenix completely](guide:topic.security#removing)
