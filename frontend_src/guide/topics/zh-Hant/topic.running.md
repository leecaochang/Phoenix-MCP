---
{ "id": "topic.running", "title": "執行 Phoenix", "parent": "topic.about", "order": 50, "keywords": ["維運", "日常", "監控", "自動化"], "sectionKeywords": { "sensors": ["感測器", "遙測", "Request Count", "Denied Count", "Rate Limit Hits", "Last Access", "Expires In", "狀態", "實體 ID", "裝置", "統計"], "events": ["事件", "自動化觸發器", "phoenix_mcp_access_key_revoked", "phoenix_mcp_access_key_rotated", "phoenix_mcp_access_key_expired", "phoenix_mcp_rate_limited", "事件資料"], "approval-events": ["phoenix_mcp_approval_requested", "phoenix_mcp_approval_resolved", "phoenix_mcp_approval_claimed", "phoenix_mcp_config_changed", "手機通知", "行動應用程式", "通知核准"], "mesa-events": ["phoenix_mcp_mesa_blocked", "phoenix_mcp_mesa_lease_expired"], "notifications": ["notification_id", "持續性通知", "關閉"], "storage": [".storage", "檔案", "備份", "phoenix_mcp_audit", "phoenix_mcp_conversations"] } }
---
Phoenix 會向 Home Assistant 新增一些內容，幫助你觀察它並圍繞它實現自動化。每個存取金鑰都會取得感測器，Phoenix 會觸發事件並顯示通知。本頁也列出 Phoenix 儲存資料的位置。

## 每個金鑰的感測器 {#sensors}

每個有效的存取金鑰都會在 Home Assistant 中顯示為一個名為 "Phoenix MCP Access key" 後接金鑰名稱的裝置。它有六個診斷感測器：

- **Status**：有效、已過期或已撤銷。
- **Request Count**：金鑰已執行的操作數，來自任何應用程式或 Phoenix 介面。
- **Denied Count**：被權限拒絕存取，或鎖定不存在實體的操作數。
- **Rate Limit Hits**：因速率限制被拒絕的請求數。
- **Last Access**：來自外部應用程式的最後一個請求，精確到分鐘。Agent Chat、語音、AI 任務和 Assist 不會改變它。
- **Expires In**：金鑰過期前的天數，永遠不過期時為未知。

每個感測器的實體 ID 是 `sensor.phoenix_mcp_access_key_`，然後是金鑰名稱和感測器名稱。對於名為 `my_access_key` 的金鑰，在英語環境下：

```text
sensor.phoenix_mcp_access_key_my_access_key_status
sensor.phoenix_mcp_access_key_my_access_key_request_count
sensor.phoenix_mcp_access_key_my_access_key_denied_count
sensor.phoenix_mcp_access_key_my_access_key_rate_limit_hits
sensor.phoenix_mcp_access_key_my_access_key_last_access
sensor.phoenix_mcp_access_key_my_access_key_expires_in
```

這三個計數會儲存在記憶體中，因此 Home Assistant 重新啟動或 Phoenix 重新載入時會從 0 重新開始。關閉日誌記錄時它們仍會繼續計數。

重新命名金鑰會重新命名其裝置，但保留實體 ID。撤銷金鑰、金鑰過期或刪除核心資料會移除裝置、其感測器及其統計。

智慧體永遠無法讀取這些感測器，即使使用直通模式。

## 自動化事件 {#events}

Phoenix 會在 Home Assistant 的事件匯流排上觸發這些事件。將它們用作自動化觸發器，例如在金鑰過期時傳送訊息。所有時間戳均為 UTC。

- `phoenix_mcp_access_key_revoked`：你撤銷了金鑰。資料：`access_key_id`、`access_key_name`、`revoked_by`、`timestamp`。
- `phoenix_mcp_access_key_rotated`：你輪換了金鑰。資料：`access_key_id`、`access_key_name`、`rotated_by`、`timestamp`。
- `phoenix_mcp_access_key_expired`：金鑰超過其有效期並被歸檔。資料：`access_key_id`、`access_key_name`、`timestamp`。
- `phoenix_mcp_rate_limited`：金鑰達到其速率限制，每個金鑰每分鐘最多一次。資料：`access_key_id`、`access_key_name`、`timestamp`。

刪除核心資料或移除整合不會為其移除的金鑰觸發已撤銷或已過期事件。

### 核准和變更 {#approval-events}

- `phoenix_mcp_approval_requested`：有請求正在等待核准。資料：`approval_id`、`access_key_id`、`access_key_name`、`tool_name`、`cap_name`、`review_url`、`expires_at`、`timestamp`。
- `phoenix_mcp_approval_resolved`：請求達到最終狀態。資料：`approval_id`、`access_key_id`、`access_key_name`、`tool_name`、`status`、`closure_reason`、`approved_by_user_id`、`timestamp`。
- `phoenix_mcp_approval_claimed`：已核准的請求開始執行（`claimed` 為 true），或在啟動失敗後被釋放（`claimed` 為 false）。資料：`approval_id`、`claimed`、`timestamp`。
- `phoenix_mcp_config_changed`：變更中記錄了一次變更。資料：`resource_type`、`resource_id`、`action`。

`status` 為 approved 表示你核准了它，而不是操作成功。安全副本審核使用存取金鑰 ID `__configuration_recovery__`。

若要取得開啟請求的手機通知，請使用如下自動化，把其中 `notify.mobile_app_your_device` 替換為你手機的 notify 操作。條件會確保連結確實指向此請求的審核頁面。

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

相關：[核准如何運作](guide:topic.approvalLifecycle)、[安全副本](guide:topic.configurationHistory#recovery)

### MESA {#mesa-events}

- `phoenix_mcp_mesa_blocked`：MESA 拒絕了某個實體，每個實體一次。資料：`access_key_id`、`access_key_name`、`entity_id`、`rule_applied`、`timestamp`。
- `phoenix_mcp_mesa_lease_expired`：MESA 租約結束。資料：`lease_id`、`entities`、`reason`、`timestamp`。原因為 `natural_expiry`、`early_release` 或 `session_terminated`。

## 通知 {#notifications}

Phoenix 可以顯示 Home Assistant 通知，自動化也可以關閉它們：

- [{{label:settings.notifyRateLimitLabel}}](guide:window.settingsLogs#notify-rate-limit)使用 ID `phoenix_mcp_rate_limit_` 後接金鑰的 ID。
- [{{label:settings.notifyApprovalLabel}}](guide:window.settingsLogs#notify-approval)使用 ID `phoenix_mcp_approval_` 後接核准的 ID。請求解決後 Phoenix 會關閉它。

## Phoenix 把資料儲存在哪裡 {#storage}

Phoenix 會把資料儲存在 Home Assistant 的 `.storage` 資料夾中，檔名以 `phoenix_mcp` 開頭。它們儲存存取金鑰和設定、稽核日誌、對話記錄、變更歷史、安全副本、MESA 策略、AI 提供者帳戶和路由統計。

Home Assistant 備份會包含它們，因此請把備份視為機密。

相關：[什麼儲存在哪裡](guide:topic.security#stored)、[完全移除 Phoenix](guide:topic.security#removing)
