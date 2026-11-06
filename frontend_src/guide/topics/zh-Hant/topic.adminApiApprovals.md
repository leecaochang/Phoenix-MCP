---
{ "id": "topic.adminApiApprovals", "title": "核准與變更端點", "parent": "topic.adminApi", "order": 20, "keywords": ["核准 API", "核准", "拒絕", "取消", "版本", "回滾", "復原", "需要關注", "卡片目錄"], "sectionKeywords": { "approvals": ["待處理", "approval_id", "PendingApproval"], "list-approvals": ["in_progress", "狀態篩選", "access_key_id"], "approve": ["approvalExecutionInterrupted", "approvalProcessing", "killSwitchEngaged", "approvalCapabilityDenied", "approvalAccessKeyInactive", "execution_failed", "refused_before_write"], "batch-approve": ["approval_ids", "批次", "applied", "remaining", "全部核准"], "reject": ["原因", "closure_reason"], "cancel": ["DELETE", "admin_cancelled"], "claims": ["phoenix_mcp_approval_claimed", "phoenix_mcp_approval_resolved", "claim", "認領", "事件"], "history": ["版本", "resource_type", "resource_id", "truncated"], "read-version": ["restore_refusals", "yaml_tags", "audit_only", "template_incompatible", "entity_missing"], "restore-version": ["expected_latest_id", "stale_version", "restore_unverified", "rollback", "side"], "attention": ["attention", "未知結果", "checked_outcome", "行事曆"], "recovery": ["config-recovery", "安全副本", "快照", "phase"], "recovery-action": ["inspect", "reviewed", "delete", "recoveryBlocked"], "recovery-restore": ["isError", "recoveryManualOnly", "zigbee"], "card-catalog": ["自訂卡片", "收集", "resource_fingerprint", "stub_config"] } }
---
這些端點處理智慧體請求你核准的事項，以及它們所做的變更：核准、設定歷史、復原記錄、需要關注和儀表板卡片目錄。登入、錯誤本文和請求體如[管理 API](guide:topic.adminApi)所述。

相關：[錯誤](guide:topic.adminApi#errors)、[請求體](guide:topic.adminApi#bodies)、[核准如何運作](guide:topic.approvalLifecycle)、[變更記錄的內容](guide:topic.configurationHistory)

## 核准 {#approvals}

```
GET    /api/phoenix-mcp/admin/approvals
GET    /api/phoenix-mcp/admin/approvals/{approval_id}
DELETE /api/phoenix-mcp/admin/approvals/{approval_id}
POST   /api/phoenix-mcp/admin/approvals/{approval_id}/approve
POST   /api/phoenix-mcp/admin/approvals/batch/approve
POST   /api/phoenix-mcp/admin/approvals/{approval_id}/reject
```

使用它們審核、核准、拒絕或取消智慧體正在等待的操作。針對單一核准的端點傳回此核准記錄：

| 欄位 | 說明 |
|---|---|
| `id` | 核准 ID。 |
| `access_key_id`、`access_key_name` | 發起請求的存取金鑰。 |
| `tool_name`、`cap_name` | 被暫緩等待核准的工具，以及它需要的能力。 |
| `args` | 工具的參數。機密、裝置代碼和每個以 `_` 開頭的鍵都顯示為 `<redacted>`。 |
| `diff` | 提議的變更。 |
| `status` | `pending`、`approved`、`rejected`、`failed`、`expired` 或 `cancelled`。 |
| `created_at`、`expires_at`、`resolved_at` | 請求時間、過期時間和結束時間。 |
| `approved_by_user_id`、`approved_at` | 誰核准了它，以及何時。 |
| `closure_reason` | 為什麼它未執行就結束，或為什麼執行失敗。取值見下文。 |
| `result` | 核准後工具的結果。 |
| `request_id`、`client_ip` | 發起請求的智慧體請求。 |
| `household_request`、`ha_user_id` | 家庭請求標誌，以及請求背後的 Home Assistant 使用者。 |
| `execution_started_at` | 核准開始執行操作的時間。 |

`closure_reason` 是你自己的拒絕原因，或以下值之一：

```text
access_key_inactive        capability_denied
kill_switch                admin_cancelled
access_key_revoked         access_key_expired
phoenix_mcp_data_wiped     execution_interrupted
execution_failed           agent_chat_ended
agent_chat_wait_timed_out  integration_removed
recovery_review_replaced   phoenix_mcp_restarted
```

相關：[核准](guide:window.approvals)、[它可能如何結束](guide:topic.approvalLifecycle#outcomes)

### 列出核准 {#list-approvals}

列出核准，最新的在前。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `status` | query, string | 僅列出狀態完全為此值的核准。未知值傳回空清單。 |
| `access_key_id` | query, string | 僅列出此存取金鑰的核准。 |
| `limit` | query, integer | 預設 50，最多 500。更大的值按 500 計。 |
| `offset` | query, integer | 預設 0。 |

回應為 `{ approvals, total, limit, offset }`。`approvals` 中的每一項都是記錄加上 `in_progress`，在核准正在執行它時為 `true`，在 Phoenix 關閉一次未回報結果的執行之前也為 `true`。即使其狀態仍為 `pending`，也應把這樣的列視為不可操作。

錯誤：[請求體](guide:topic.adminApi#bodies)中的分頁錯誤。

### 讀取單一核准 {#read-approval}

傳回核准記錄，不含 `in_progress`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.approvalNotFound` | 沒有核准具有此 ID。 |

### 核准 {#approve}

核准該核准記錄，並依智慧體的請求執行暫緩的操作。該端點不接受本文，並忽略你傳送的任何本文。

Phoenix 依下文錯誤表的順序檢查核准。執行任何內容之前，它會向磁碟儲存一個執行標記。如果某次執行到達操作卻從未回報，該標記會保留，之後每次核准、拒絕或取消都會傳回 `409`。

MESA 為確認而暫緩的核准，以及復原審核，會跳過能力檢查。復原審核還會跳過緊急停止開關檢查。

回應為 `200`，包含更新後的記錄：

- 操作執行時 `status: "approved"`。
- 工具傳回錯誤時 `status: "failed"`，且 `closure_reason: "execution_failed"`。這仍是 `200`。
- 工具已傳送變更但無法確認是否生效時 `status: "failed"`，且 `closure_reason: "execution_interrupted"`。重試前請先檢查結果。這仍是 `200`。
- `result` 為 `{ tool_result, outcome }`。當工具在寫入任何內容之前拒絕時，它會新增 `refused_before_write: true`。
- 已經結束的核准會原樣傳回。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 409 | `conflict` | `adminError.approvalWipeInProgress` | Phoenix 資料正在被刪除。 |
| 404 | `not_found` | `adminError.approvalNotFound` | 沒有核准具有此 ID，或它在執行時消失了。 |
| 409 | `conflict` | `adminError.approvalProcessing` | 另一個核准正在執行它。 |
| 409 | `conflict` | `adminError.approvalExecutionInterrupted` | 較早的核准已到達操作，但從未回報。在做其他任何事情之前，請檢查結果。 |
| 409 | `conflict` | `adminError.approvalExpired` | 它已超過 `expires_at`，因此本次呼叫以 `expired` 關閉它。沒有執行任何內容。 |
| 409 | `conflict` | `adminError.approvalAlreadyResolved` | 另一個請求先完成了它。 |
| 404 | `not_found` | `adminError.approvalAccessKeyInactive` | 存取金鑰不再有效。Phoenix 以 `cancelled` 關閉核准，原因為 `access_key_inactive`。 |
| 403 | `forbidden` | `adminError.approvalCapabilityDenied` | 此金鑰的能力現在設為拒絕存取。Phoenix 以 `failed` 關閉核准，原因為 `capability_denied`。 |
| 503 | `service_unavailable` | `adminError.killSwitchEngaged` | 緊急停止開關已開啟。Phoenix 以 `cancelled` 關閉核准，原因為 `kill_switch`。 |
| 503 | `service_unavailable` | `adminError.approvalRecordFailed` | Phoenix 無法儲存執行標記。沒有執行任何內容，核准仍為待處理。 |
| 400 | `invalid_request` | `adminError.approvalExecutorMissing` | Phoenix 沒有執行此工具的方式。沒有執行任何內容。 |
| 500 | `internal_error` | `adminError.approvalExecutionUncertain` | 執行引發錯誤。Phoenix 以 `failed` 關閉核准，原因為 `execution_interrupted`，並且不會再提供它。 |
| 500 | `internal_error` | `adminError.approvalOutcomeNotSaved` | 操作已完成，但 Phoenix 無法儲存其結果。Phoenix 不會再提供此核准，之後以 `failed` 關閉它，原因為 `execution_interrupted`。它的 `approval/executed` 或 `approval/failed` 稽核條目帶有 `finalization: "not_saved"`，記錄了實際發生的情況。 |

其中三個錯誤聽起來是暫時的，但會永久關閉核准：`approvalAccessKeyInactive`、`approvalCapabilityDenied` 和 `killSwitchEngaged`。之後修復條件也不會讓它恢復。

### 核准多個 {#batch-approve}

依序逐個核准多個核准，經過與單一核准相同的步驟。批次會在第一個未傳回 `200` 且 `status: "approved"` 的核准處停止。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `approval_ids` | 字串陣列 | 必填。1 到 50 個 ID，每個為非空字串，不能重複。 |

回應一律為 `200`：

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

- `applied` 列出已執行的核准。已經核准的核准也算作已套用。
- 當每個核准都執行時，`failed` 為 `null`。其 `status` 是該項目的 HTTP 狀態。其 `error` 是該項目的錯誤代碼，否則是其 `closure_reason`，否則為 `"failed"`。
- 當項目的錯誤帶有 `message_key` 和 `message_params` 時，`failed` 也會攜帶它們。
- `remaining` 列出批次未嘗試的 ID。

批次會新增一筆稽核日誌條目，而不是每個核准一筆。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.approvalIdsNonEmpty` | `approval_ids` 缺少、為空，或包含非空字串以外的內容。 |
| 400 | `invalid_request` | `adminError.approvalIdsUnique` | 某個 ID 重複。 |
| 400 | `invalid_request` | `adminError.approvalBatchMaximum` | 超過 50 個 ID。`message_params.max` 為 50。 |

### 拒絕 {#reject}

拒絕待處理的核准，使操作永不執行。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `reason` | string | 選填。儲存在 `closure_reason` 中。 |

回應為更新後的記錄，`status: "rejected"`。已經結束的核准會以 `200` 原樣傳回。超過 `expires_at` 的待處理核准會改為以 `expired` 關閉。

拒絕復原審核還會記錄你已檢查它。如果復原記錄拒絕這一點，你會收到 `409` `conflict`，並帶有[對記錄執行操作](guide:#recovery-action)中的某個鍵。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.approvalReasonString` | `reason` 不是字串。 |
| 404 | `not_found` | `adminError.approvalNotFound` | 沒有核准具有此 ID。 |
| 409 | `conflict` | `adminError.approvalProcessing` | 核准正在執行它。 |
| 409 | `conflict` | `adminError.approvalExecutionInterrupted` | 較早的核准已到達操作，但從未回報。請先檢查結果。 |

### 取消 {#cancel}

取消待處理的核准。它的運作方式與拒絕相同，`status: "cancelled"`，`closure_reason: "admin_cancelled"`。不傳送本文。

回應以及已結束和逾期核准的處理與[拒絕](guide:#reject)相同。錯誤相同，但 `approvalReasonString` 除外。

### 認領和事件 {#claims}

Phoenix 在 Home Assistant 的事件匯流排上觸發兩個事件，以便腳本無需輪詢即可追蹤核准：

- 當一次核准取得該記錄時，在操作執行前觸發 `phoenix_mcp_approval_claimed`，`claimed: true`。資料：`approval_id`、`claimed`、`timestamp`。
- 如果執行前的失敗使核准保持待處理，它會再次以 `claimed: false` 觸發。
- 核准達到最終狀態時觸發 `phoenix_mcp_approval_resolved`。資料：`approval_id`、`access_key_id`、`access_key_name`、`tool_name`、`status`、`closure_reason`、`approved_by_user_id`、`timestamp`。

執行期間載入的頁面會在[列出核准](guide:#list-approvals)的列上看到 `in_progress: true`。

相關：[自動化事件](guide:topic.running#events)

## 設定歷史 {#history}

```
GET    /api/phoenix-mcp/admin/versions
GET    /api/phoenix-mcp/admin/versions/{version_id}
POST   /api/phoenix-mcp/admin/versions/{version_id}/restore
```

使用它們讀取智慧體和還原所做的變更，並把變更的其中一側放回去。

Phoenix 記錄這些資源類型的版本：

```text
automation  script  scene      helper     dashboard  entity  device
blueprint   energy  config_entry  area    floor      label   category
```

每個版本都有一個操作：`create`、`edit`、`delete` 或 `rollback`。

Phoenix 保留每個資源最新的 20 個版本。超過 100,000 位元組的原始文字會存為 `{ "content": null, "truncated": true, "bytes": <size> }`，且無法還原。

相關：[變更](guide:window.changes)、[變更記錄的內容](guide:topic.configurationHistory)

### 列出設定版本 {#list-versions}

列出所有資源的近期變更，或一個資源的所有版本。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `resource_type` | query, string | 與 `resource_id` 一起使用時，列出該資源儲存的每個版本，最新的在前，不分頁。 |
| `resource_id` | query, string | 與 `resource_type` 一起傳送，或兩者都省略以取得近期清單。 |
| `limit` | query, integer | 僅用於近期清單。預設 50，最多 500。更大的值按 500 計。 |
| `offset` | query, integer | 僅用於近期清單。預設 0。 |

回應為 `{ resource_type, resource_id, versions, total }`。`versions` 中的每一項都是摘要：

| 欄位 | 說明 |
|---|---|
| `id` | 版本 ID。 |
| `resource_type`、`resource_id`、`alias` | 變更了什麼，以及其名稱。 |
| `action` | `create`、`edit`、`delete` 或 `rollback`。 |
| `access_key_id`、`access_key_name` | 做出變更的存取金鑰。 |
| `approved_by_user_id` | 核准變更或執行還原的管理員。 |
| `timestamp` | 變更時間。 |
| `summary`、`summary_key`、`summary_params` | 簡短描述，以及其翻譯鍵和值。 |
| `has_before`、`has_after` | 版本是否包含變更前或變更後的一側。 |

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.versionResourcePairRequired` | `resource_type` 和 `resource_id` 只傳送了一個。 |

[請求體](guide:topic.adminApi#bodies)中的分頁錯誤也適用。

### 讀取單一版本 {#read-version}

完整傳回一個版本：摘要欄位（不含 `has_before` 和 `has_after`），加上 `before`、`after` 和 `request_id`。

它會新增 `restore_refusals: { before, after }`。每一側為 `null`，或 Phoenix 拒絕還原該側的原因：`yaml_tags`、`audit_only`、`template_incompatible` 或 `entity_missing`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.versionNotFound` | 沒有版本具有此 ID。 |

### 還原一個版本 {#restore-version}

把一個版本的其中一側套用到目前設定。空本文也可以。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `side` | string | 選填。`"before"` 或 `"after"`。預設 `"after"`，當版本沒有變更後一側時預設 `"before"`。 |
| `expected_latest_id` | string 或 null | 選填。設定後，除非這是資源最新版本的 ID，否則還原不會套用任何內容。 |

傳送 `expected_latest_id` 可確保你上次讀取歷史後沒有其他變更到達。如果有，你會收到 `stale_version`；請再次讀取歷史並決定。

還原以你的管理員權限執行。Phoenix 會把它記錄為一個 `rollback` 版本，`approved_by_user_id` 為你。稽核日誌條目會以 `originating_access_key_id` 保留做出原始變更的存取金鑰。

回應為 `200`，包含 `{ "restored": true, version_id, resource_type, resource_id }`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.versionNotFound` | 沒有版本具有此 ID。 |
| 400 | `invalid_request` | `adminError.restoreSideInvalid` | `side` 不是 `"before"` 或 `"after"`。 |
| 400 | `invalid_request` | `adminError.stringOrNull` | `expected_latest_id` 不是字串或 null。`message_params.field` 為 `expected_latest_id`。 |
| 409 | `stale_version` | `adminError.restoreStale` | `expected_latest_id` 不再是最新版本。沒有套用任何內容。 |
| 500 | `internal_error` | `adminError.restoreFailed` | 還原引發錯誤。 |
| 409 | `restore_unverified` | `adminError.restoreUnverified` | Phoenix 傳送了還原，但讀回的資源不符合。它可能已部分套用，因此重試前請檢查。 |
| 400 | `invalid_request` | `adminError.restoreNoConfigSide` | 該側沒有可還原的設定。 |
| 400 | `invalid_request` | `adminError.restoreYamlTags` | 版本包含 `!secret` 或 `!include` 等 YAML 標籤。請手動重新建立。 |
| 400 | `invalid_request` | 某個復原鍵 | 復原記錄拒絕還原。相關鍵列在[對記錄執行操作](guide:#recovery-action)中。 |
| 400 | `invalid_request` | `adminError.restoreToolReported` | 還原工具因其他原因拒絕。`message_params.detail` 帶有其自己的英文文字。 |
| 400 | `invalid_request` | `adminError.restoreNotApplicable` | 還原無法套用到目前設定。 |

## 需要關注 {#attention}

```
GET    /api/phoenix-mcp/admin/attention
POST   /api/phoenix-mcp/admin/attention/{kind}/{record_id}
```

使用它們尋找 Phoenix 不知道結果的智慧體和行事曆操作，並在你查看後把它們標記為已檢查。

相關：[需要關注](guide:window.attention)

### 列出項目 {#attention-items}

回應為 `{ items }`。每個項目是以下之一：

- 結果未知的智慧體操作：`{ id, "kind": "agent", tool, access_key_name, started_at, dispatch_result }`。
- 結果未知的行事曆寫入：`{ id, "kind": "calendar", tool, access_key_name, started_at, entity_id, "dispatch_result": "unknown" }`。其 `tool` 為 `<operation>_calendar_event`。
- 當 Phoenix 無法讀取其行事曆記錄時：`{ "id": "calendar_storage", "kind": "calendar_storage" }`。
- 當 Phoenix 上一次儲存稽核記錄失敗時：`{ "id": "audit_storage", "kind": "audit_storage" }`。
- 當 MESA 啟動失敗時：`{ "id": "mesa", "kind": "mesa" }`。
- 當重新命名實體的策略已生效但儲存失敗時：`{ "id": "mesa_rename", "kind": "mesa_rename" }`。

請求寫入行事曆時，其行事曆項目會替換同一請求的智慧體項目。

### 標記操作已檢查 {#attention-review}

記錄你已檢查某個智慧體或行事曆操作的結果。在路徑中使用項目的 `kind` 和 `id`。Phoenix 只儲存你的審核；它不會再次執行或復原該操作。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `checked_outcome` | boolean | 必填。必須完全為 `true`。 |

Phoenix 會再次從 Home Assistant 讀取你的使用者，你仍必須是有效管理員。回應為 `{ "reviewed": true }`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.attentionCheckRequired` | `checked_outcome` 不為 `true`。 |
| 403 | `forbidden` | `adminError.adminRequired` | 你不再是有效管理員。 |
| 404 | `not_found` | `adminError.attentionNotReviewable` | `kind` 不是 `agent` 或 `calendar`。 |
| 503 | `service_unavailable` | `adminError.attentionSaveFailed` | Phoenix 無法儲存審核。該操作仍需審核。 |
| 409 | `conflict` | `adminError.attentionReviewConflict` | 操作仍在執行，或已有人審核過它。請重新載入清單。 |

## 設定復原 {#recovery}

```
GET    /api/phoenix-mcp/admin/config-recovery
POST   /api/phoenix-mcp/admin/config-recovery/{recovery_id}
```

使用它們讀取 Phoenix 在設定寫入前保留的安全副本，並檢查、清除或還原它們。

相關：[安全副本](guide:topic.configurationHistory#recovery)

### 列出復原記錄 {#recovery-records}

傳回 `{ records }`，最新的在前，不分頁。每筆記錄有這些欄位：

| 欄位 | 說明 |
|---|---|
| `id` | 復原 ID。 |
| `resource`、`operation` | 寫入變更了什麼，以及如何變更。 |
| `access_key_id` | 做出寫入的存取金鑰。 |
| `created_at` | Phoenix 取得副本的時間。 |
| `phase` | `prepared`、`dispatching`、`partial`、`unknown`、`verified`、`not_applied` 或 `reviewed`。 |
| `before`、`source`、`expected` | 儲存的副本，以及寫入原本應產生的內容。 |
| `observations` | 階段變化後 Phoenix 觀察到的內容。 |
| `reviewed_by` | 有人審核後，審核者。 |
| `active` | 寫入仍在執行時為 `true`。 |

Phoenix 始終保留有效記錄和處於 `unknown` 的記錄。其餘記錄保留 30 天，每個資源最多 20 筆，總共 256 筆。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 503 | `service_unavailable` | `adminError.recoveryStorageUnavailable` | Phoenix 無法載入其復原記錄。 |

### 對記錄執行操作 {#recovery-action}

透過 `action` 選擇檢查、審核、刪除或還原一筆記錄。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `action` | string | 必填。`inspect`、`reviewed`、`delete` 或 `restore`。 |
| `side` | string | 僅用於 `restore`。`"before"` 或 `"source"`。 |

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.recoveryNotFound` | 沒有記錄具有此 ID。 |
| 400 | `invalid_request` | `adminError.recoveryUnknownAction` | `action` 不是四者之一。 |
| 400 | `invalid_request` | `adminError.recoverySideRequired` | `side` 缺少，或該側沒有儲存的副本。 |
| 400 | `invalid_request` | `adminError.recoveryManualOnly` | Phoenix 無法還原此類副本。請手動還原。 |
| 409 | `conflict` | 某個復原鍵 | 記錄拒絕該操作。鍵名說明原因。 |

復原鍵為 `adminError.recoveryStorageUnavailable`、`adminError.recoveryOperationActive`、`adminError.recoveryCommandRunning`、`adminError.recoveryResolveInApprovals`、`adminError.recoveryFull`、`adminError.recoveryReviewBeforeRemoving`、`adminError.recoveryReviewBeforeRestoring`、`adminError.recoveryChangedAfterReview`、`adminError.recoveryAuthorityChanged`、`adminError.recoveryHelperUnreadable`、`adminError.recoverySnapshotUnsafe` 和 `adminError.recoverySnapshotUnsafeAt`。任何其他拒絕使用 `adminError.recoveryBlocked`。`adminError.recoverySnapshotUnsafeAt` 在 `message_params.location` 中提供被拒絕的位置。

### 檢查 {#recovery-inspect}

讀取記錄所涵蓋內容的目前狀態。它絕不證明是哪個請求造成了該狀態。回應取決於副本類型：

- 重新啟動回執：`{ runtime_replaced, "execution_attribution": "unproven" }`。
- 儲存輔助項目：`{ observed, helper_present, "execution_attribution": "unproven" }`。
- 模板輔助項目：`{ observed, entry_present, "execution_attribution": "unproven" }`。
- 其他任何類型：`{ "status": "manual_verification_required" }`。

### 審核和刪除 {#recovery-review}

`reviewed` 把記錄標記為已審核，並傳回 `{ "status": "reviewed" }`。`delete` 刪除它，並傳回 `{ "status": "delete" }`。刪除要求階段為 `verified`、`not_applied` 或 `reviewed`。

寫入仍在執行，或 Phoenix 傳送的某個 Home Assistant 命令尚未完成時，兩者都會被拒絕。

### 還原 {#recovery-restore}

把所選的一側寫回。記錄必須處於非有效狀態，階段為 `verified`、`not_applied` 或 `reviewed`，並且該側有儲存的副本。

- Phoenix 會還原儲存輔助項目、模板輔助項目、能源偏好、藍圖來源，以及儀表板登錄檔、佈局和資源副本。
- 設定檔文字的副本一律會被拒絕，因為 Phoenix 不會把檔案文字寫回。
- 其他所有類型，包括重新啟動回執和 Zigbee 副本，都會傳回 `400` 和 `adminError.recoveryManualOnly`。

還原傳回 `200`，包含 `{ result, outcome }`，其中 `result` 是還原工具的結果。被拒絕或失敗的還原也會傳回 `200`，`result.isError: true`，並帶有工具自己的文字。請檢查 `result`，而不是狀態代碼。

## 儀表板卡片目錄 {#card-catalog}

```
GET    /api/phoenix-mcp/admin/card_catalog
POST   /api/phoenix-mcp/admin/card_catalog
```

卡片目錄列出瀏覽器找到的自訂儀表板卡片。面板會傳送報告；你很少需要這樣做。

### 讀取目錄 {#read-catalog}

回應為 `{ harvested, entries, harvested_at, resource_count, failed_imports, resource_fingerprint }`。在瀏覽器報告之前，`harvested` 會保持 `false`。這不等於沒有自訂卡片。

`entries` 中的每一項有 `type`、`name`、`description`、`documentation_url`、`preview`、`available`、`has_visual_editor`、`stub_config` 和 `source`。`source` 為 `picker` 或 `element`。

### 報告收集結果 {#report-catalog}

用瀏覽器找到的內容替換整個目錄。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `entries` | array | 必填。最多保留 500 個條目，每個 `type` 一個。`type` 最多 120 個字元。超過 2000 位元組的 `stub_config` 會被捨棄。 |
| `resource_count` | number | 選填。瀏覽器看到的儀表板資源數量。 |
| `failed_imports` | `{ url, error }` 的陣列 | 選填。最多保留 50 個。 |

Phoenix 會清理每個欄位，並跳過錯誤條目，而不是失敗。它會自行讀取儀表板資源，並儲存 `resource_fingerprint`，即對其類型和 URL 的 SHA-256。當 Phoenix 無法讀取它們時，指紋為 `null`。

回應為 `{ harvested, harvested_at, cards, available, failed_imports }`。最後三個是計數。

面板每個瀏覽器和面板版本最多每 10 分鐘報告一次。當沒有找到資源也沒有找到卡片時，它會跳過報告，並且只在出現更多卡片時再次報告。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.entriesListRequired` | `entries` 缺少或不是陣列。 |
