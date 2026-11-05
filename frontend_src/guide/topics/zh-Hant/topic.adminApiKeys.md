---
{ "id": "topic.adminApiKeys", "title": "存取金鑰端點", "parent": "topic.adminApi", "order": 10, "keywords": ["存取金鑰 API", "權杖 API", "phx_", "建立金鑰", "權限 API", "能力 API"], "sectionKeywords": { "access-keys": ["存取金鑰記錄", "金鑰清單"], "capability-fields": ["cap_", "deny", "allow", "confirm", "能力欄位"], "key-create": ["新金鑰", "access_key", "原始值", "201"], "key-change": ["PATCH", "角色", "備註", "重新命名", "unknownFields"], "key-revoke": ["刪除金鑰", "撤銷", "phoenix_mcp_access_key_revoked"], "key-rotate": ["輪換", "新值", "phoenix_mcp_access_key_rotated"], "key-stats": ["request_count", "denied_count", "rate_limit_hits"], "key-audit": ["稽核列", "結果篩選", "用戶端 IP"], "key-archived": ["已歸檔金鑰", "已過期金鑰", "已撤銷金鑰"], "presets": ["預設", "已儲存設定", "access_key_presets_enabled"], "permissions": ["權限樹", "節點", "域", "裝置", "實體"], "permissions-replace": ["PUT", "替換樹", "清除全部"], "permissions-node": ["單一節點", "設定狀態"], "bulk-select": ["分區", "分類", "整合", "選擇器"], "integration-options": ["設定條目", "整合清單"], "entity-hints": ["提示", "全域提示", "entity_hints"], "entities": ["實體樹", "範圍", "解析", "force_reload"], "resolve": ["resolution_path", "有效權限", "NO_ACCESS", "NOT_FOUND"], "scope": ["readable", "writable", "capability_flags"] } }
---
這些端點管理存取金鑰：建立金鑰、變更其設定和能力，以及設定每個金鑰可以讀取和控制什麼。每個路徑都以 `/api/phoenix-mcp/admin` 開頭，並且需要管理員。

請求體、錯誤信封和 ID 規則如[管理 API](guide:topic.adminApi)所述。下方的錯誤表列出每個端點自己的錯誤。來自[請求體](guide:topic.adminApi#bodies)的本文錯誤適用於每個接受本文的端點。

相關：[錯誤](guide:topic.adminApi#errors)、[路徑中的 ID](guide:topic.adminApi#ids)、[權限狀態](guide:topic.adminApi#states)

## 存取金鑰 {#access-keys}

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

使用它們建立、變更、輪換和撤銷金鑰，以及讀取其使用情況。`{id}` 是存取金鑰的 ID。

每個指定金鑰的端點在金鑰不存在時傳回 `404`：

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

### 存取金鑰記錄 {#key-record}

大多數端點傳回金鑰的記錄。它絕不包含金鑰本身。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `id` | string | 金鑰的 ID。 |
| `name` | string | 金鑰的名稱。 |
| `created_at` | string | ISO 8601 時間。 |
| `created_by` | string | 建立它的管理員的 Home Assistant 使用者 ID。 |
| `expires_at` | string 或 null | ISO 8601 時間，或 `null` 表示無有效期。 |
| `revoked` | boolean | |
| `last_used_at` | string 或 null | 智慧體上次使用金鑰的時間。 |
| `updated_at` | string | 金鑰的最後變更。 |
| `pass_through` | boolean | 參見[直通模式](guide:topic.passThrough)。 |
| `use_assist_exposure` | boolean | 僅在 `pass_through` 為 `true` 時出現。 |
| `announce_all_tools` | boolean | |
| `confirm_inline_wait_seconds` | integer | 關閉時為 `0`。 |
| `rate_limit_requests` | integer | 每分鐘請求數。`0` 會關閉速率限制。 |
| `rate_limit_burst` | integer | 每秒請求數。 |
| `persona` | string | 角色標籤。 |
| `notes` | string 或 null | 給智慧體的備註。 |
| `permissions` | object | 權限樹，如[權限](guide:#permissions)所述。 |
| `presets` | array | 已儲存的預設，如[預設](guide:#presets)所述。 |
| `active_preset_id` | string 或 null | 金鑰符合的預設。 |
| `cap_*` | string | 每項能力一個欄位：`deny`、`allow` 或 `confirm`。 |

### 能力欄位 {#capability-fields}

每個金鑰都有這 32 個欄位。每個欄位接受 `deny`、`allow` 或 `confirm`，但只有標記為「是」的欄位接受 `confirm`。

| 欄位 | 接受 `confirm` | 能力 |
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

相關：[拒絕存取、允許和確認](guide:window.capabilities#modes)

### 列出金鑰 {#key-list}

`GET /access-keys` 傳回有效金鑰記錄的裸 JSON 陣列。已撤銷和已過期的金鑰在[歸檔](guide:#key-archived)中。

### 建立金鑰 {#key-create}

`POST /access-keys` 建立金鑰。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `name` | string | 必填。3 到 32 個字母、數字、`-` 或 `_`。它必須不同於每個有效金鑰的名稱，忽略大小寫，並把 `-` 和 `_` 視為相同。已歸檔金鑰的名稱可以重複使用。 |
| `pass_through` | boolean | 預設 `false`。 |
| `confirm_pass_through` | boolean | 當 `pass_through` 為 `true` 時必須為 `true`。 |
| `use_assist_exposure` | boolean | 預設 `false`。僅在 `pass_through` 為 `true` 時保留。 |
| `expires_at` | string | 選填。未來的 ISO 8601 時間。沒有時區時，Phoenix 依 Home Assistant 的本地時間讀取，並以 UTC 儲存。省略該欄位表示無有效期；`null` 會被拒絕。 |
| `rate_limit_requests` | integer | 0 到 100000（預設 60）。設為 0 可關閉速率限制。 |
| `rate_limit_burst` | integer | 0 到 100000（預設 10）。當 `rate_limit_requests` 為 0 時存為 0。 |

建立只讀取這些欄位。新金鑰從角色 `custom`、每項能力 `deny`、空權限樹、`confirm_inline_wait_seconds` 為 0、`announce_all_tools` 關閉開始。之後用 [PATCH](guide:#key-change) 設定其餘內容。

回應為 `201`，包含新記錄以及 `access_key`，即金鑰本身：`phx_` 後接 64 個字元。Phoenix 僅在此回應中顯示它，請立即儲存。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nameRequired` | 缺少 `name`。 |
| 400 | `invalid_request` | `adminError.accessKeyNameLengthPattern` | `name` 違反命名規則。`message_params` 包含 `min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | `pass_through` 或 `use_assist_exposure` 不是布林值。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.passThroughConfirmationRequired` | `pass_through` 為 `true`，但沒有 `confirm_pass_through: true`。 |
| 400 | `invalid_request` | `adminError.badExpiresAt` | `expires_at` 不是可用的時間。 |
| 400 | `invalid_request` | `adminError.expiresInPast` | `expires_at` 不在未來。 |
| 400 | `invalid_request` | `adminError.rateLimitsInteger` | 速率限制不是整數。 |
| 400 | `invalid_request` | `adminError.rateLimitsNonNegative` | 速率限制為負數。 |
| 400 | `invalid_request` | `adminError.rateLimitsMaximum` | 速率限制超過 `max`（100000）。 |
| 409 | `conflict` | `adminError.accessKeyNameTaken` | 已有有效金鑰使用此名稱。 |

### 讀取金鑰 {#key-read}

`GET /access-keys/{id}` 傳回金鑰的記錄。

### 變更金鑰 {#key-change}

`PATCH /access-keys/{id}` 變更這些欄位的任意子集。回應為 `200`，包含更新後的記錄。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `name` | string | 與建立時相同的規則，並與每個其他有效金鑰比較。 |
| `pass_through` | boolean | 對已關閉該選項的金鑰開啟它，還需要 `confirm_pass_through: true`。 |
| `confirm_pass_through` | boolean | 參見 `pass_through`。 |
| `use_assist_exposure` | boolean | 僅當金鑰最終開啟 `pass_through` 時允許。 |
| `announce_all_tools` | boolean | |
| `confirm_inline_wait_seconds` | integer | 0（關閉）或 30 到 180。 |
| `rate_limit_requests` | integer | 0 到 100000。設為 0 也會把 `rate_limit_burst` 設為 0。 |
| `rate_limit_burst` | integer | 0 到 100000。 |
| `persona` | string | 以下之一：`home_companion`、`read_only`、`voice_assistant`、`automation_builder`、`power_user`、`home_admin`、`new_user`、`dashboard_designer`、`maintenance`、`esphome`、`custom`。 |
| `notes` | string 或 null | 最多 2000 個字元，在行尾變成 LF 並去除首尾空白之後。控制字元、雙向覆寫字元和標籤字元會被拒絕。`null` 或 `""` 會清除備註。 |
| `cap_*` | string | 任意[能力欄位](guide:#capability-fields)：`deny`、`allow` 或 `confirm`。 |

PATCH 拒絕不在此表中的任何欄位，且 `expires_at` 根本無法變更。

`persona` 設定金鑰的角色標籤。它不會變更任何能力，因此請在同一請求中傳送你想要的能力值。

角色為 `home_companion` 的金鑰一律關閉 `pass_through` 和 `announce_all_tools`。要求開啟其中任一都會被拒絕。

重新命名金鑰會變更其感測器和裝置在 Home Assistant 中的顯示名稱。它們的實體 ID 保持不變。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.expiresImmutable` | 本文包含 `expires_at`。Phoenix 會在尋找金鑰之前檢查這一點。 |
| 400 | `invalid_request` | `adminError.unknownFields` | 本文包含表中沒有的欄位。`message_params` 以 `fields` 列出它們。 |
| 400 | `invalid_request` | `adminError.nameRequired` | `name` 為空。 |
| 400 | `invalid_request` | `adminError.accessKeyNameLengthPattern` | `name` 違反命名規則。`message_params` 給出 `min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.accessKeyNameTaken` | 另一個有效金鑰已使用此名稱。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | 某個布林欄位使用了其他類型。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.passThroughConfirmationRequired` | `pass_through` 開啟但沒有 `confirm_pass_through: true`。 |
| 400 | `invalid_request` | `adminError.assistExposurePassThroughOnly` | 沒有直通模式卻使用了 `use_assist_exposure`。 |
| 400 | `invalid_request` | `adminError.inlineWaitRange` | `confirm_inline_wait_seconds` 超出範圍。`message_params` 給出 `min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.integerRequired` | 速率限制不是整數。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.nonNegativeRequired` | 速率限制為負數。`message_params` 指明 `field`。 |
| 400 | `invalid_request` | `adminError.maximumRequired` | 速率限制超過 100000。`message_params` 給出 `field` 和 `max`。 |
| 400 | `invalid_request` | `adminError.unknownPersona` | `persona` 不是列出的值之一。 |
| 400 | `invalid_request` | `adminError.companionSettingRefused` | Home Companion 金鑰要求 `pass_through` 或 `announce_all_tools`。 |
| 400 | `invalid_request` | `adminError.notesString` | `notes` 不是字串或 `null`。 |
| 400 | `invalid_request` | `adminError.notesTooLong` | `notes` 超過 `max` 個字元。 |
| 400 | `invalid_request` | `adminError.notesInvalidCharacters` | `notes` 包含被拒絕的字元。 |
| 400 | `invalid_request` | `adminError.capabilityModeInvalid` | 某個 `cap_*` 值不是 `deny`、`allow` 或 `confirm`。`message_params` 指明 `capability`。 |
| 400 | `invalid_request` | `adminError.capabilityConfirmUnsupported` | 對不接受 `confirm` 的能力使用了 `confirm`。`message_params` 指明 `capability`。 |
| 400 | `invalid_request` | `adminError.capabilityAllowUnsupported` | 對一律需要核准的 `cap_dashboard_resources` 使用了 `allow`。`message_params` 指明 `capability`。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

### 撤銷金鑰 {#key-revoke}

`DELETE /access-keys/{id}` 立即撤銷金鑰並將它移到歸檔。回應為 `204`，沒有本文。Phoenix 還會：

- 從 Assist、語音智慧體和 AI 任務綁定中移除金鑰。Phoenix 為該金鑰建立的 Assist 管線或 AI 任務實體也會被移除。
- 取消該金鑰排隊的核准，並釋放其 MESA 租約。
- 捨棄其速率限制狀態和請求計數，並移除其感測器。
- 觸發 `phoenix_mcp_access_key_revoked` 事件，包含 `access_key_id`、`access_key_name`、`revoked_by` 和 `timestamp`。

相關：[{{label:accessKeys.revoke}}](guide:window.accessKeys#revoke)

### 輪換金鑰 {#key-rotate}

`POST /access-keys/{id}/rotate` 為金鑰提供新值，不接受本文。舊值立即失效。金鑰保留其速率限制狀態和計數。

回應為 `200`，包含記錄以及 `access_key`，即新值。與建立時一樣，Phoenix 只顯示一次。Phoenix 觸發 `phoenix_mcp_access_key_rotated` 事件，包含 `access_key_id`、`access_key_name`、`rotated_by` 和 `timestamp`。

相關：[{{label:accessKeys.rotate}}](guide:window.accessKeys#rotate)

### 金鑰統計 {#key-stats}

`GET /access-keys/{id}/stats` 傳回金鑰的請求計數。每次 Home Assistant 重新啟動後，計數器從零開始。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `access_key_id` | string | |
| `access_key_name` | string | |
| `request_count` | integer | 自重新啟動以來的請求數。 |
| `denied_count` | integer | 自重新啟動以來被拒絕存取的請求數。 |
| `rate_limit_hits` | integer | 自重新啟動以來因速率限制被拒絕的請求數。 |
| `last_used_at` | string 或 null | |
| `status` | string | `active` 或 `expired`。 |

### 連線檢查 {#key-connection}

`GET /access-keys/{id}/connection` 傳回 `last_used_at` 和 `request_count`。智慧體用有效金鑰傳送的每個請求都會增加 `request_count`，因此輪詢此端點可查看應用程式是否已連線。

### 金鑰稽核日誌 {#key-audit}

`GET /access-keys/{id}/audit` 傳回金鑰稽核列的裸陣列，最新的在前。管理員對金鑰所做的變更會以管理員身分記錄，因此不會出現在這裡。

| 查詢參數 | 說明 |
|---|---|
| `limit` | 最多 500（預設 100）。 |
| `offset` | 要跳過的列數。 |
| `outcome` | 以下之一：`allowed`、`denied`、`not_found`、`rate_limited`、`not_implemented`、`invalid_request`、`pending_approval`。 |
| `ip` | 符合用戶端 IP 包含此文字的列。 |

每一列有 `request_id`、`timestamp`、`access_key_id`、`access_key_name`、`method`、`resource`、`outcome`、`client_ip` 和 `pass_through`。設定後，列還會包含 `payload`、`mesa_advisory`、`preset`、`stale_tools_advisory`、`ha_user_id` 和 `originating_access_key_id`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination` | `limit` 或 `offset` 不是整數。 |
| 400 | `invalid_request` | `adminError.paginationNonNegative` | `limit` 或 `offset` 為負數。 |
| 400 | `invalid_request` | `adminError.outcomeFilterUnknown` | `outcome` 不是列出的值之一。`message_params` 給出 `value`。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

### 已歸檔金鑰 {#key-archived}

`GET /access-keys/archived` 傳回已撤銷和已過期金鑰的裸陣列。每個條目有 `id`、`name`、`created_at`、`created_by`、`revoked_at`、`revoked`、`expires_at` 和 `last_used_at`。已過期的金鑰顯示 `revoked: false`。

`DELETE /access-keys/archived/{id}` 永久刪除已歸檔記錄。回應為 `204`，沒有本文。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.archivedAccessKeyNotFound` | 沒有已歸檔記錄具有此 ID。 |

## 預設 {#presets}

```
POST   /api/phoenix-mcp/admin/access-keys/{id}/presets
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}
DELETE /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}
POST   /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}/apply
```

預設會儲存金鑰的設定，以便你之後切回。`{pid}` 是預設的 ID。每個預設端點都傳回金鑰的記錄。

預設預設為關閉。請先開啟 `access_key_presets_enabled` 設定。在此之前，每個預設端點都會在尋找金鑰之前傳回 `403`。

預設會儲存金鑰的設定：`caps`（全部 32 項能力）、`persona`、`permissions`、`pass_through`、`use_assist_exposure` 和 `announce_all_tools`。它還儲存 `confirm_inline_wait_seconds`、`rate_limit_requests` 和 `rate_limit_burst`，以及自己的 `id`、`name` 和 `created_at`。金鑰的名稱和備註不屬於其中。

每個金鑰最多儲存 8 個預設。預設名稱去除首尾空白後為 1 到 40 個字元，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;`。同一金鑰內的名稱必須不同，忽略大小寫。

每個預設端點還可能傳回：

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 403 | `forbidden` | `adminError.presetsDisabled` | `access_key_presets_enabled` 已關閉。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

### 儲存預設 {#preset-create}

`POST /access-keys/{id}/presets` 把金鑰的目前設定儲存為新預設，並使其成為目前預設。回應為 `201`。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `name` | string | 必填。上方的預設名稱規則。 |

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nameRequired` | 缺少 `name`。 |
| 400 | `invalid_request` | `adminError.presetNameTooLong` | `name` 超過 `max` 個字元。 |
| 400 | `invalid_request` | `adminError.presetNameInvalidCharacters` | `name` 包含被拒絕的字元。 |
| 400 | `invalid_request` | `adminError.presetNameTaken` | 金鑰已有使用此名稱的預設。 |
| 400 | `invalid_request` | `adminError.presetLimitReached` | 金鑰已有 8 個預設。`message_params` 包含 `max`。 |
| 400 | `invalid_request` | `adminError.presetSaveRejected` | Phoenix 拒絕了傳送的預設。 |

### 重新命名預設 {#preset-rename}

`PATCH /access-keys/{id}/presets/{pid}` 重新命名預設。本文和名稱錯誤與儲存時相同。回應為 `200`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetRenameRejected` | 此金鑰的另一個預設使用了該名稱。 |
| 404 | `not_found` | `adminError.presetNotFound` | 金鑰沒有此 ID 的預設。 |

### 刪除預設 {#preset-delete}

`DELETE /access-keys/{id}/presets/{pid}` 刪除非目前預設。它不接受本文，並傳回 `200` 和記錄，而不是 `204`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetDeleteRejected` | 該預設是目前預設。 |
| 404 | `not_found` | `adminError.presetNotFound` | 金鑰沒有此 ID 的預設。 |

### 套用預設 {#preset-apply}

`POST /access-keys/{id}/presets/{pid}/apply` 讓金鑰符合預設，並將其標記為目前。回應為 `200`。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `confirm_pass_through` | boolean | 選填。當預設為已關閉直通模式的金鑰開啟直通模式時，必須為 `true`。 |

在切換到不同預設之前，Phoenix 會把金鑰的目前設定儲存到目前預設中。再次套用目前預設會把金鑰恢復為該預設儲存的狀態。

套用會設定角色、直通模式、Assist 公開範圍、工具宣告、同步等待核准、速率限制、能力和權限樹。它還會重新開始金鑰的速率限制計數。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetPassThroughConfirmationRequired` | 預設開啟直通模式，但本文缺少 `confirm_pass_through: true`。 |
| 404 | `not_found` | `adminError.presetNotFound` | 金鑰沒有此 ID 的預設。 |

## 權限 {#permissions}

```
GET    /api/phoenix-mcp/admin/access-keys/{id}/permissions
PUT    /api/phoenix-mcp/admin/access-keys/{id}/permissions
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/domains/{node}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/devices/{node}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/entities/{node}
GET    /api/phoenix-mcp/admin/access-keys/{id}/permissions/integration-options
POST   /api/phoenix-mcp/admin/access-keys/{id}/permissions/bulk-select
```

這些端點讀取和設定權限樹：金鑰可以讀取和控制哪些域、裝置和實體。每個節點有一個 `state` 和一個選填的 `hint`，供智慧體使用。

```json
{
  "domains":  { "light": { "state": "YELLOW", "hint": null } },
  "devices":  { "<device_id>": { "state": "GREEN", "hint": "Desk lamp" } },
  "entities": { "light.desk": { "state": "RED", "hint": null } }
}
```

節點 ID 遵循[路徑中的 ID](guide:topic.adminApi#ids)：域在 `domains` 下，裝置 ID 在 `devices` 下，實體 ID 在 `entities` 下。狀態參見[權限狀態](guide:topic.adminApi#states)。

相關：[權限如何判定](guide:topic.permissionResolution)、[{{label:accessKeys.permissionsTree}}](guide:window.permissions)

### 讀取權限樹 {#permissions-read}

`GET /access-keys/{id}/permissions` 傳回權限樹。

### 替換權限樹 {#permissions-replace}

`PUT /access-keys/{id}/permissions` 用本文替換整個權限樹。回應為 `200`，包含新權限樹。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `domains`、`devices`、`entities` | object | 每個都把節點 ID 映射到節點物件。Phoenix 忽略其他頂層欄位。 |
| `state` | string | 在每個節點中。預設為 `GREY`。 |
| `hint` | string 或 null | 在每個節點中。最多 200 個字元，按傳送內容原樣保留。 |

要清除所有權限，請傳送 `{"domains": {}, "devices": {}, "entities": {}}`。

相關：[{{label:workspace.clearAllPermissions}}](guide:window.permissions#clear-all)

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.permissionSectionObject` | 某個 `section` 不是物件。 |
| 400 | `invalid_request` | `adminError.permissionNodeObject` | 某個 `node` 不是物件。 |
| 400 | `invalid_request` | `adminError.nodeIdTooLong` | 某個節點 ID 超過 255 個字元。 |
| 400 | `invalid_request` | `adminError.nodeIdInvalidChars` | 某個節點 ID 包含被拒絕的字元。 |
| 400 | `invalid_request` | `adminError.badDomain` | 某個域 ID 不是有效域。 |
| 400 | `invalid_request` | `adminError.badEntityId` | 某個實體 ID 不是有效實體 ID。 |
| 400 | `invalid_request` | `adminError.permissionStateInvalidDomain`、`adminError.permissionStateInvalidDevice` 或 `adminError.permissionStateInvalidEntity` | 節點的 `state` 不是四種之一。鍵名指明節點類型；`message_params` 給出 `state`、`node` 和 `states`。 |
| 400 | `invalid_request` | `adminError.permissionHintString` | 節點的 `hint` 不是字串或 `null`。 |
| 400 | `invalid_request` | `adminError.permissionHintTooLong` | 節點的 `hint` 超過 `max` 個字元。 |
| 400 | `invalid_request` | `adminError.permissionTreeInvalid` | 權限樹無法按傳送內容儲存。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

### 設定一個節點 {#permissions-node}

`PATCH /access-keys/{id}/permissions/domains/{node}`、`.../devices/{node}` 和 `.../entities/{node}` 設定一個節點。`{node}` 是域、裝置 ID 或實體 ID。回應為 `200`，包含整個權限樹。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `state` | string | 必填。四種狀態之一。 |
| `hint` | string 或 null | 選填。最多 200 個字元。省略它可保留節點的提示；`null` 或 `""` 會清除它。 |

在沒有提示的節點上設定 `GREY` 會移除該節點。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nodeIdTooLong` | `{node}` 超過 255 個字元。Phoenix 會先檢查節點 ID。 |
| 400 | `invalid_request` | `adminError.nodeIdInvalidChars` | `{node}` 包含被拒絕的字元。 |
| 400 | `invalid_request` | `adminError.badDomain` | `{node}` 不是有效域。 |
| 400 | `invalid_request` | `adminError.badEntityId` | `{node}` 不是有效實體 ID。 |
| 400 | `invalid_request` | `adminError.stateAllowed` | `state` 缺少或不是四種之一。`message_params` 列出 `states`。 |
| 400 | `invalid_request` | `adminError.hintString` | `hint` 不是字串或 `null`。 |
| 400 | `invalid_request` | `adminError.hintTooLong` | `hint` 超過 `max` 個字元。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

### 整合選項 {#integration-options}

`GET /access-keys/{id}/permissions/integration-options` 列出可與[批次選取](guide:#bulk-select)一起使用的整合。回應為 `{"integrations": [...]}`，依標題、域和條目 ID 排序。

每個至少擁有一個裝置或一個無裝置的線上實體的設定條目對應一列：

| 欄位 | 類型 | 說明 |
|---|---|---|
| `entry_id` | string | 用作 `selector_id`。 |
| `domain` | string | 整合的域。 |
| `title` | string | 條目的標題。 |
| `device_count` | integer | |
| `deviceless_entity_count` | integer | |
| `device_ids` | array | |
| `entity_ids` | array | 沒有裝置的線上、已啟用實體。 |
| `registry_only_deviceless_count` | integer | 沒有裝置且沒有線上狀態的實體。 |
| `required_domain_ids` | array | |
| `shared_device_count` | integer | |

### 批次選取 {#bulk-select}

`POST /access-keys/{id}/permissions/bulk-select` 為分區、分類或整合中的所有內容設定一種狀態。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `selector_type` | string | `area`、`label` 或 `integration`。 |
| `selector_id` | string | 必填。分區 ID、分類 ID 或設定條目 ID。 |
| `state` | string | 四種狀態之一。 |

- 整合會設定其裝置節點及其沒有裝置的實體節點。
- 分區或分類只設定實體節點，適用於分區或分類符合的每個實體。實體的分類包括其裝置的分類。
- 現有提示會保留。使用 `GREY` 時，沒有提示的節點會被移除，有提示的節點保持為 `GREY`。

回應包含新權限樹和摘要：

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

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.selectorTypeInvalid` | `selector_type` 不是三者之一。 |
| 400 | `invalid_request` | `adminError.selectorIdRequired` | `selector_id` 缺少或為空。 |
| 400 | `invalid_request` | `adminError.stateAllowed` | `state` 不是四種之一。`message_params` 列出 `states`。 |
| 404 | `not_found` | `adminError.permissionSelectionNotFound` | 條目不存在，或沒有實體符合該分區或分類。Phoenix 會在尋找金鑰之前檢查這一點。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

相關：[{{label:selectBy.menu}}](guide:window.permissions#select-by)

## 實體提示 {#entity-hints}

```
GET    /api/phoenix-mcp/admin/entity-hints
PUT    /api/phoenix-mcp/admin/entity-hints/{entity_id}
```

全域提示會告訴每個智慧體某個實體的情況，無論使用哪個金鑰。金鑰自己權限節點上的提示優先於全域提示。

智慧體會先看到金鑰實體節點上的提示，然後是裝置節點，再是域節點，最後才是全域提示。Phoenix 會移除看起來像機密的文字，並把金鑰無法存取的實體 ID 替換為 `<redacted-entity>`。

相關：[實體提示](guide:window.permissions#hints)

### 讀取全域提示 {#hints-read}

`GET /entity-hints` 傳回每個全域提示：

```json
{ "entity_hints": { "light.desk": "Desk lamp by the window" } }
```

### 設定全域提示 {#hints-set}

`PUT /entity-hints/{entity_id}` 設定或清除一個提示。回應為整個 `entity_hints` 映射。Phoenix 只檢查實體 ID 的格式，不檢查實體是否存在。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `hint` | string 或 null | 去除首尾空白後最多 200 個字元。`null`、`""` 或沒有 `hint` 欄位會清除它。 |

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | `{entity_id}` 不是有效實體 ID。 |
| 400 | `invalid_request` | `adminError.hintString` | `hint` 不是字串或 `null`。 |
| 400 | `invalid_request` | `adminError.hintTooLong` | `hint` 超過 `max` 個字元。 |

## 實體 {#entities}

```
GET    /api/phoenix-mcp/admin/entities
GET    /api/phoenix-mcp/admin/access-keys/{id}/resolve/{entity_id}
GET    /api/phoenix-mcp/admin/access-keys/{id}/scope
```

這些端點顯示權限編輯器所看到的你的家，以及一個金鑰實際可以存取什麼。

### 實體樹 {#entity-tree}

`GET /entities` 傳回每個實體，依域和裝置分組。它不分頁。Phoenix 會快取該樹；新增任何非空值的 `force_reload` 可重建它。

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

- 已停用的實體、`phoenix_mcp` 域和 Phoenix 自己的感測器會被排除。
- 有狀態但沒有登錄檔條目的實體會出現在 `deviceless_entities` 中。
- 子裝置的主裝置會出現在子裝置所在的每個域中。
- `person`、`tag` 和 `zone` 一律存在，即使為空。
- 實體的 `labels` 包括其裝置的分類。

### 解釋一項權限 {#resolve}

`GET /access-keys/{id}/resolve/{entity_id}` 顯示 Phoenix 如何判定金鑰對某個實體的權限。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `entity_id` | string | |
| `resolution_path` | array | `{level, state}` 步驟，最外層在前。 |
| `effective` | string | `WRITE`、`READ`、`DENY`、`NO_ACCESS` 或 `NOT_FOUND`。 |
| `effective_hint` | string 或 null | 智慧體看到的提示，順序見[實體提示](guide:#entity-hints)。 |

`level` 的取值依次為 `global`（始終為 `GREY`）、`domain:<domain>`，然後是對實體之上每個裝置的 `device:<name>`（主裝置在前），最後是 `entity:<entity_id>`。裝置層級顯示裝置名稱，而不是其 ID。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | `{entity_id}` 不是有效實體 ID。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有有效金鑰具有此 ID。 |

相關：[權限如何判定](guide:topic.permissionResolution)

### 金鑰範圍 {#scope}

`GET /access-keys/{id}/scope` 列出金鑰可以讀取和控制的實體。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `access_key_id` | string | |
| `access_key_name` | string | |
| `readable` | array | 實體 ID，已排序。 |
| `writable` | array | 實體 ID，已排序。 |
| `persona` | string | |
| `capability_flags` | object | 金鑰儲存的能力值，在直通模式套用之前。 |
