---
{ "id": "topic.adminApiSettings", "title": "設定與系統端點", "parent": "topic.adminApi", "order": 50, "keywords": ["設定", "配置", "系統", "清除", "重設", "資訊", "版本", "目錄", "翻譯"], "sectionKeywords": { "settings": ["PATCH", "設定物件"], "settings-read": ["計算欄位", "tool_routing_qualified_languages", "esphome_builder"], "settings-change": ["驗證", "副作用"], "writable-settings": ["預設", "範圍", "鉗制", "kill_switch", "mesa_mode"], "api-only": ["shadow", "工具審核", "observe", "advise", "local_shadow", "hybrid", "command_routing_languages", "command_routing_timeout_ms", "decision_shadow_enabled"], "wipe": ["刪除全部", "恢復出廠設定", "WIPE", "wipe_core", "wipe_providers", "wipe_mesa"], "confirmations": ["確認", "確認欄位"], "info": ["版本", "工具數量", "home_companion_reach"], "catalog": ["語言", "地區設定", "字串", "翻譯"], "audit": ["請求日誌", "稽核日誌", "篩選"], "conversations": ["對話記錄", "逐字稿", "歷史"], "voice-pipeline": ["Assist 管線", "語音助理", "慣用助理"], "ai-task": ["AI 任務", "資料產生", "預設實體"], "routing": ["命令路由", "辨識", "預覽", "統計", "請求預算", "決策提供者"] } }
---
使用這些端點讀取和變更 Phoenix 的全域設定、清除其資料，以及讀取其日誌、版本和路由計數。每個端點都遵循[管理 API](guide:topic.adminApi)概述中的規則。

相關：[錯誤](guide:topic.adminApi#errors)、[Agent Chat 端點](guide:topic.adminApiChat)

## 設定 {#settings}

```
GET    /api/phoenix-mcp/admin/settings
PATCH  /api/phoenix-mcp/admin/settings
```

讀取和變更設定工作區背後的設定。每項設定對操作員意味著什麼，請參見其視窗頁面：[一般](guide:window.settingsGeneral)、[安全](guide:window.settingsSafety)、[日誌](guide:window.settingsLogs)、[Agent Chat](guide:window.settingsChat)、[Home Assistant](guide:window.settingsHome) 和 [AI 提供者](guide:window.settingsProviders)。

### 讀取設定 {#settings-read}

`GET /api/phoenix-mcp/admin/settings` 傳回每項[可寫設定](guide:#writable-settings)以及這些唯讀欄位：

- `tool_routing_qualified_languages`
- `assist_api_supported`
- `voice_agent_pipeline_supported`
- `voice_agent_pipeline_preferred`
- `voice_agent_pipeline_id`，僅由[語音智慧體助理](guide:#voice-pipeline)路由設定
- `ai_task_supported`
- `esphome_integration`、`esphome_builder` 和 `esphome_builder_live`

### 變更設定 {#settings-change}

`PATCH /api/phoenix-mcp/admin/settings` 接受可寫設定的任意子集。沒有設定需要確認欄位，`kill_switch` 也不例外。

Phoenix 在寫入任何內容之前檢查每個值。第一個問題會拒絕整個請求，不會發生任何變更。

已接受的變更會有這些效果：

- 它會取消正在進行的決策工作，並使進行中的路由結果和命令核准失效。
- 開啟 `access_key_presets_enabled` 會給每個金鑰一個名為 Default 的預設。
- 關閉 `kill_switch` 會重新開啟智慧體路由。
- Phoenix 會立即套用 `mesa_mode`，並更新語音智慧體、AI 任務和 Assist 工具提供方。
- `mesa_inject_enabled` 和 `agentcli_global` 會在頁面下次完整載入時生效。

稽核日誌會記錄變更，包含已變更設定的名稱，而不是其值。回應與讀取相同。當語音智慧體不再完全設定好，但 Home Assistant 保留 Phoenix 的助理作為其唯一助理時，它會新增 `voice_agent_pipeline_kept: true`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.unknownFields` | 某個欄位不可寫，例如 `voice_agent_pipeline_id`。`message_params` 包含 `fields`。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | 某個布林設定收到了其他類型。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.valueAllowed` | 某個選擇設定收到了其他值：`mesa_mode`、某種風格、某個詳細級別、某種路由模式或某個稽核選單。`message_params` 包含 `field` 和 `values`。 |
| 400 | `invalid_request` | `adminError.integerRequired` | 某個整數設定收到了其他類型。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.routingLanguagesInvalid` | `command_routing_languages` 指定了不是已安裝 Assist 語言的語言。 |
| 400 | `invalid_request` | `adminError.conversationLimitInvalid` | 對話記錄限制、`command_routing_timeout_ms`、`decision_timeout_ms` 或 `decision_request_budget` 超出範圍。`message_params` 包含 `field`、`min` 和 `max`。 |
| 400 | `invalid_request` | `adminError.accessKeyIdOrNull` | 某個存取金鑰設定不是字串或 `null`。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.activeAccessKeyRequired` | 某個存取金鑰設定指定的不是有效金鑰。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.stringOrNull` | 某個模型設定不是字串或 `null`。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.configuredProviderRequired` | 某個提供者設定指定的帳戶不存在。`message_params` 包含 `field`。 |
| 400 | `invalid_request` | `adminError.decisionProviderRequired` | 在沒有決策帳戶的情況下開啟了某個決策功能。 |
| 400 | `invalid_request` | `adminError.toolRoutingUnqualified` | 為沒有合格語言的模型把工具審核模式設為 `advise`。 |

### 可寫設定 {#writable-settings}

「拒絕」表示超出範圍的值傳回 `400`。「鉗制」表示 Phoenix 將其移到最近的邊界。「決策帳戶」是決策類型的帳戶，「對話帳戶」是其他任何帳戶。

| 設定 | 類型和範圍 | 預設值 | 說明 |
|---|---|---|---|
| `kill_switch` | boolean | `false` | 關閉它會開啟智慧體路由。開啟時，聊天路由傳回 `503`。參見[緊急停止開關](guide:window.settingsSafety#kill-switch)。 |
| `disable_all_logging` | boolean | `false` | 面板以相反方式顯示它，即 **{{label:settings.logEnableLabel}}**。參見[日誌](guide:window.settingsLogs#log-enable)。 |
| `log_allowed` | boolean | `true` | 參見[日誌](guide:window.settingsLogs#log-allowed)。 |
| `log_denied` | boolean | `true` | 參見[日誌](guide:window.settingsLogs#log-denied)。 |
| `log_rate_limited` | boolean | `true` | 參見[日誌](guide:window.settingsLogs#log-rate-limited)。 |
| `log_entity_names` | boolean | `true` | 參見[日誌](guide:window.settingsLogs#log-entity-ids)。 |
| `log_client_ip` | boolean | `true` | 參見[日誌](guide:window.settingsLogs#log-client-ip)。 |
| `notify_on_rate_limit` | boolean | `false` | 參見[日誌](guide:window.settingsLogs#notify-rate-limit)。 |
| `notify_on_approval` | boolean | `true` | 參見[日誌](guide:window.settingsLogs#notify-approval)。 |
| `audit_flush_interval` | integer：0、5、10、15、30 或 60 分鐘 | 15 | 否則拒絕。接受數字字串。參見[日誌](guide:window.settingsLogs#log-flush)。 |
| `audit_log_maxlen` | integer：100、1000、5000 或 10000 | 10000 | 否則拒絕。接受數字字串。參見[日誌](guide:window.settingsLogs#log-max-entries)。 |
| `conversation_log_enabled` | boolean | `false` | 參見[日誌](guide:window.settingsLogs#conversation-log)。 |
| `conversation_log_max_entries` | integer，1 到 2000 | 100 | 拒絕。需要 JSON 整數。參見[日誌](guide:window.settingsLogs#conversation-entries)。 |
| `conversation_log_max_days` | integer，1 到 365 | 7 | 拒絕。需要 JSON 整數。參見[日誌](guide:window.settingsLogs#conversation-days)。 |
| `conversation_log_max_mb` | integer，1 到 250（十進位 MB） | 25 | 拒絕。需要 JSON 整數。參見[日誌](guide:window.settingsLogs#conversation-size)。 |
| `mesa_mode` | `off`、`advisory` 或 `enforced` | `advisory` | 參見[安全](guide:window.settingsSafety#mesa-mode)。 |
| `mesa_inject_enabled` | boolean | `false` | 在頁面下次完整載入時生效。參見[一般](guide:window.settingsGeneral#inject)。 |
| `access_key_presets_enabled` | boolean | `false` | 開啟會給每個金鑰一個名為 Default 的預設。參見[一般](guide:window.settingsGeneral#presets)。 |
| `agentcli_global` | boolean | `true` | 在頁面下次完整載入時生效。參見[Agent Chat](guide:window.settingsChat#chat-global)。 |
| `agentcli_scrollback_lines` | integer，0 到 5000 | 100 | 鉗制。接受數字字串。參見[Agent Chat](guide:window.settingsChat#chat-memory)。 |
| `agentcli_max_iterations` | integer，3 到 100 | 20 | 鉗制。參見[Agent Chat](guide:window.settingsChat#chat-steps)。 |
| `agentcli_conversation_style` | `direct`、`warm`、`calm_guide`、`lively` 或 `technical` | `direct` | 參見[Agent Chat](guide:window.settingsChat#chat-style)。 |
| `agentcli_detail_level` | `concise`、`balanced` 或 `detailed` | `concise` | 參見[Agent Chat](guide:window.settingsChat#chat-detail)。 |
| `agentcli_home_focused` | boolean | `false` | 偏好，不是安全控制。參見[Agent Chat](guide:window.settingsChat#chat-home-focused)。 |
| `agentcli_routing_mode` | `off`、`auto`、`local` 或 `hybrid` | `off` | 面板只寫入 `off` 和 `auto`。`hybrid` 需要決策帳戶。參見[Agent Chat](guide:window.settingsChat#chat-commands)和[僅 API 設定](guide:#api-only)。 |
| `agentcli_routing_shadow_enabled` | boolean | `false` | 僅 API。參見[僅 API 設定](guide:#api-only)。 |
| `voice_agent_routing_mode` | `off`、`auto`、`local_shadow`、`local` 或 `hybrid` | `off` | 面板只寫入 `off` 和 `auto`。參見[Home Assistant](guide:window.settingsHome#voice-commands)和[僅 API 設定](guide:#api-only)。 |
| `command_routing_languages` | 路由語言標籤清單，無重複 | `["en"]` | 僅 API。空清單會暫停使用它的模式中的辨識。參見[僅 API 設定](guide:#api-only)。 |
| `command_routing_timeout_ms` | integer，50 到 5000 | 500 | 僅 API。拒絕。需要 JSON 整數。 |
| `decision_provider_id` | 決策帳戶 ID 或 `null` | `null` | 正在使用的決策帳戶。設定它也會設定預設值。`null` 會關閉影子觀察、所有工具審核和每個 `hybrid` 模式。參見[AI 提供者](guide:window.settingsProviders#decision-provider)。 |
| `decision_provider_default_id` | 決策帳戶 ID 或 `null` | `null` | 選定的預設值，在決策提供者關閉時保留。在正在使用帳戶時設定它會切換到該帳戶。 |
| `decision_shadow_enabled` | boolean | `false` | 僅 API。開啟它需要決策帳戶。參見[僅 API 設定](guide:#api-only)。 |
| `decision_timeout_ms` | integer，250 到 30000 | 5000 | 拒絕。需要 JSON 整數。參見[AI 提供者](guide:window.settingsProviders#decision-timeout)。 |
| `decision_request_budget` | integer，每小時 0 到 100000 | 0 | 0 表示不限。拒絕。需要 JSON 整數。參見[AI 提供者](guide:window.settingsProviders#decision-request-limit)。 |
| `agentcli_tool_routing_mode` | `off`、`observe` 或 `advise` | `off` | 僅 API。參見[僅 API 設定](guide:#api-only)。 |
| `voice_agent_tool_routing_mode` | `off`、`observe` 或 `advise` | `off` | 僅 API。參見[僅 API 設定](guide:#api-only)。 |
| `ai_task_tool_routing_mode` | `off`、`observe` 或 `advise` | `off` | 僅 API。參見[僅 API 設定](guide:#api-only)。 |
| `provider_catalog_refresh_enabled` | boolean | `true` | 參見[AI 提供者](guide:window.settingsProviders#provider-checks)。 |
| `provider_catalog_refresh_hours` | integer，6 到 720 | 24 | 鉗制。參見[AI 提供者](guide:window.settingsProviders#provider-checks-hours)。 |
| `assist_bound_access_key_id` | 有效存取金鑰 ID 或 `null` | `null` | 空字串也會清除它。參見[Home Assistant](guide:window.settingsHome#assist-access-key)。 |
| `voice_agent_enabled` | boolean | `false` | 參見[Home Assistant](guide:window.settingsHome#voice-enable)。 |
| `voice_agent_access_key_id` | 有效存取金鑰 ID 或 `null` | `null` | 參見[Home Assistant](guide:window.settingsHome#voice-access-key)。 |
| `voice_agent_provider_id` | 對話帳戶 ID 或 `null` | `null` | 參見[Home Assistant](guide:window.settingsHome#voice-provider)。 |
| `voice_agent_model` | string 或 `null` | `null` | Phoenix 不會對照提供者檢查它。參見[Home Assistant](guide:window.settingsHome#voice-model)。 |
| `voice_agent_conversation_style` | 同 `agentcli_conversation_style` | `direct` | 參見[Home Assistant](guide:window.settingsHome#voice-style)。 |
| `voice_agent_detail_level` | 同 `agentcli_detail_level` | `concise` | 參見[Home Assistant](guide:window.settingsHome#voice-detail)。 |
| `voice_agent_home_focused` | boolean | `false` | 參見[Home Assistant](guide:window.settingsHome#voice-home-focused)。 |
| `ai_task_enabled` | boolean | `false` | 參見[Home Assistant](guide:window.settingsHome#ai-task-enable)。 |
| `ai_task_access_key_id` | 有效存取金鑰 ID 或 `null` | `null` | 參見[Home Assistant](guide:window.settingsHome#ai-task-access-key)。 |
| `ai_task_provider_id` | 對話帳戶 ID 或 `null` | `null` | 參見[Home Assistant](guide:window.settingsHome#ai-task-provider)。 |
| `ai_task_model` | string 或 `null` | `null` | 參見[Home Assistant](guide:window.settingsHome#ai-task-model)。 |
| `ai_task_conversation_style` | 同 `agentcli_conversation_style` | `direct` | 參見[Home Assistant](guide:window.settingsHome#ai-task-style)。 |
| `ai_task_detail_level` | 同 `agentcli_detail_level` | `balanced` | 參見[Home Assistant](guide:window.settingsHome#ai-task-detail)。 |

## 沒有面板控制項的設定 {#api-only}

面板沒有這些設定和值的控制項。只能透過 API 設定它們。

- `agentcli_routing_mode` 的 `local` 和 `hybrid`：面板都顯示為開啟。`hybrid` 需要決策帳戶。
- `voice_agent_routing_mode` 的 `local_shadow`、`local` 和 `hybrid`。使用決策帳戶時，`auto` 本身按 `hybrid` 運作；否則按 `local` 運作。
- `agentcli_routing_shadow_enabled`：在聊天路由關閉時，僅以觀察方式執行 Agent Chat 命令辨識。任何把任一路由模式設為 `off` 或 `auto` 的變更都會關閉它。
- `command_routing_languages`：用於 `local_shadow`、`local`、`hybrid` 和背景觀察的語言，來自已安裝的 Assist 句子語言加上 `zh-Hans` 和 `zh-Hant`。`auto` 則使用每個已安裝的 Assist 語言。
- `command_routing_timeout_ms`：本機辨識可以花費的時間，以毫秒為單位。
- `decision_shadow_enabled`：把辨識遺漏的語音智慧體命令傳送給決策提供者，僅作為觀察。它只能與 `voice_agent_routing_mode` 的 `local` 或 `local_shadow` 一起運作。

三種工具審核模式要求決策提供者在模型提議的每次工具呼叫執行前檢查它。它們是 `agentcli_tool_routing_mode`、`voice_agent_tool_routing_mode` 和 `ai_task_tool_routing_mode`。

- `observe` 和 `advise` 需要決策帳戶。
- `advise` 還需要合格語言。`tool_routing_qualified_languages` 列出它們；為空時，Phoenix 會以 `adminError.toolRoutingUnqualified` 拒絕 `advise`。
- Home Companion 金鑰跳過工具審核。

相關：[命令如何路由](guide:topic.commandRouting)

## 清除資料 {#wipe}

```
DELETE /api/phoenix-mcp/admin/wipe
```

依範圍刪除 Phoenix 的資料。此操作無法復原。

```json
{"confirm": "WIPE", "wipe_core": true, "wipe_providers": true, "wipe_mesa": false}
```

| 欄位 | 類型 | 說明 |
|---|---|---|
| `confirm` | string | 必填。必須完全為 `"WIPE"`。 |
| `wipe_core` | boolean | 預設 `true`。 |
| `wipe_providers` | boolean | 預設 `true`。 |
| `wipe_mesa` | boolean | 預設 `false`。 |

缺少旗標，或旗標不是布林值，會使用其預設值。因此僅 `{"confirm": "WIPE"}` 就會清除核心資料和提供者帳戶。

`wipe_core` 會清除：

- 存取金鑰、已歸檔金鑰、實體提示，以及所有重設為預設值的設定。
- 待處理核准。Phoenix 會關閉其通知並回報它們已解決。
- 稽核日誌、對話記錄、版本歷史，以及結果未知的智慧體寫入記錄。
- 路由統計、決策請求計數、卡片目錄快取和路由釐清。
- 速率限制計數、請求計數和通知標記。
- 每個金鑰的感測器和過期計時器。Phoenix 也會還原智慧體限時變更的整合日誌級別。
- Phoenix Assist 管線。

之後，Phoenix 會套用每個已變更的預設值，例如 MESA 模式、語音智慧體、AI 任務和新增到 Home Assistant 頁面的模組。如果智慧體路由已關閉，它會開啟它們。

`wipe_providers` 會清除每個 Agent Chat 和決策帳戶及其 API 金鑰。它還會清除使用它們的設定。即語音智慧體和 AI 任務的提供者與模型，以及正在使用的決策帳戶及其預設值。影子觀察、工具審核和每個 `hybrid` 模式也會關閉。

`wipe_mesa` 會清除每條 MESA 策略和已忽略的建議。

沒有哪個旗標會清除設定復原記錄、行事曆寫入記錄或儀表板資源記錄。請使用單獨的端點移除已解決的復原記錄。

回應為 `204`，沒有本文。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.wipeConfirmationRequired` | `confirm` 不完全是 `"WIPE"`。 |
| 409 | `conflict` | `adminError.approvalWipeInProgress` | 有核准正在執行。請等待它完成。僅在使用 `wipe_core` 時。 |
| 409 | `conflict` | `adminError.approvalWipeExecutionUncertain` | 某個核准可能已套用但未回報。請先檢查其結果。僅在使用 `wipe_core` 時。 |
| 503 | `service_unavailable` | `adminError.mesaSaveFailed` | Phoenix 無法儲存 MESA 變更。僅限 `wipe_mesa`。 |

相關：[安全設定](guide:window.settingsSafety#data-management)

### 確認欄位 {#confirmations}

只有三個路由在本文中要求確認。設定 PATCH 不要求任何確認。

| 路由 | 本文 |
|---|---|
| [清除資料](guide:#wipe) | `{"confirm": "WIPE"}` 加上選填的範圍旗標 |
| [清除對話記錄](guide:#clear-conversations) | 完全為 `{"confirm": true}` |
| [重設路由統計](guide:#reset-statistics) | 完全為 `{"surface": "voice", "confirm": true}`，或 `"chat"` |

## 版本和工具 {#info}

```
GET    /api/phoenix-mcp/admin/info
```

傳回 Phoenix 的版本以及它向智慧體提供什麼。

| 欄位 | 說明 |
|---|---|
| `version` | Phoenix 的版本。 |
| `min_ha_version` | Phoenix 支援的最舊 Home Assistant 版本。 |
| `github_url` | 專案儲存庫。 |
| `tool_count` | `total`、`native` 和 `additional`，依完整工具目錄計數，與開啟了哪些功能無關。 |
| `home_companion_reach` | Home Companion 金鑰的 `control_domains`、`read_domains` 和 `sensor_classes`。 |

## 面板字串 {#catalog}

```
GET    /api/phoenix-mcp/admin/catalog/{language}
```

傳回面板某種語言的字串。與所有其他端點不同，它在 Phoenix 仍在啟動時就會回應。

回應為 `{"language": "<code>", "resources": {...}}`，使用扁平的點分鍵。Phoenix 會選擇它提供的最接近語言，然後回退到英文。未知語言會以英文傳回，`"language": "en"`。未翻譯或預留位置不符合的字串會以英文傳回。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.catalogNotFound` | 找不到目錄。僅當英文目錄缺少時才會發生。 |

## 稽核日誌 {#audit}

```
GET    /api/phoenix-mcp/admin/audit
```

分頁讀取智慧體請求日誌，最新的在前。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `limit` | integer | 預設 100，最多 500。 |
| `offset` | integer | 參見[請求體](guide:topic.adminApi#bodies)。 |
| `access_key_id` | string | 精確符合。 |
| `outcome` | string | 精確符合：`allowed`、`denied`、`not_found`、`rate_limited`、`not_implemented`、`invalid_request` 或 `pending_approval`。 |
| `ip` | string | 用戶端位址的一部分。 |
| `method` | string | 方法的一部分，不區分大小寫。 |
| `resource` | string | 資源的一部分，不區分大小寫。 |
| `request_id` | string | 精確符合。 |
| `since` | string | ISO 時間。沒有時區的時間按本地時間計算。 |

回應包含 `entries` 和 `total`。每個條目有 `request_id`、`timestamp`、`access_key_id`、`access_key_name`、`method`、`resource`、`outcome`、`client_ip` 和 `pass_through`。它還可能包含 `payload`、`mesa_advisory`、`preset`、`stale_tools_advisory`、`ha_user_id` 和 `originating_access_key_id`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination`、`adminError.paginationNonNegative` | `limit` 或 `offset` 不是大於等於 0 的整數。 |
| 400 | `invalid_request` | `adminError.sinceTimestampInvalid` | `since` 不是有效時間。 |
| 400 | `invalid_request` | `adminError.outcomeFilterUnknown` | `outcome` 不是上方的值之一，或為空。`message_params` 包含 `value`。 |

相關：[{{label:shell.tabAudit}}](guide:window.auditLog)、[日誌記錄](guide:topic.logRecords)

## 對話記錄 {#conversations}

```
GET    /api/phoenix-mcp/admin/conversations
DELETE /api/phoenix-mcp/admin/conversations
GET    /api/phoenix-mcp/admin/conversations/{entry_id}
```

讀取和清除已記錄的對話。使用 `conversation_log_enabled` 開啟記錄。

相關：[對話記錄](guide:window.conversationLog)

### 列出對話 {#list-conversations}

`GET /api/phoenix-mcp/admin/conversations`

| 欄位 | 類型 | 說明 |
|---|---|---|
| `limit` | integer | 預設 25，最多 100。 |
| `offset` | integer | 參見[請求體](guide:topic.adminApi#bodies)。 |
| `search` | string | 最多 512 個字元。符合已記錄內容，不區分大小寫。 |
| `source` | string | 最多 64 個字元：`mcp`、`agentcli`、`assist`、`voice`、`ai_task` 或 `home_companion`。 |
| `outcome` | string | 最多 64 個字元。一個狀態值。`failed` 和 `denied` 也符合結果混合的輪次。 |
| `since` | string | ISO 時間。沒有時區的時間按本地時間計算。 |
| `grouped` | string | 為分組列設為 `true`。 |

回應包含 `entries`、`total`、`enabled`、`available`、`bytes` 和 `max_bytes`。條目最新的在前。

每個條目有 `id`、`source`、`started_at`、`updated_at`、`finished_at`、`coverage`、`status` 和 `execution_status`。它還有 `response_status`、`tool_count`、`change_count`、`omissions`、`omitted_events`、`first_tool`、`excerpt` 和 `prompt_attachments`。

分組條目還有 `latest_at`。Agent Chat 執行緒會新增 `turns`（最早的在前）和 `history_missing`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination`、`adminError.paginationNonNegative` | `limit` 或 `offset` 不是大於等於 0 的整數。 |
| 400 | `invalid_request` | `adminError.sinceTimestampInvalid` | `since` 不是有效時間。 |

### 開啟對話 {#open-conversation}

`GET /api/phoenix-mcp/admin/conversations/{entry_id}` 傳回完整的已儲存記錄，包含其事件以及任何內容被省略的原因。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.conversationNotFound` | 沒有對話具有此 ID，或它已過期。 |

### 清除對話記錄 {#clear-conversations}

`DELETE /api/phoenix-mcp/admin/conversations` 只接受 `{"confirm": true}`，並傳回 `{"cleared": true}`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.conversationClearConfirm` | 本文不完全是 `{"confirm": true}`。 |
| 503 | `service_unavailable` | `adminError.conversationStorageUnavailable` | 日誌已在記憶體中清除，但儲存失敗。請重試。 |

## 語音智慧體助理 {#voice-pipeline}

```
POST   /api/phoenix-mcp/admin/voice_agent/pipeline
DELETE /api/phoenix-mcp/admin/voice_agent/pipeline
POST   /api/phoenix-mcp/admin/voice_agent/pipeline/preferred
DELETE /api/phoenix-mcp/admin/voice_agent/pipeline/preferred
```

建立和移除使用 Phoenix 語音智慧體的 Home Assistant 助理，並選擇它是否為慣用助理。

相關：[Home Assistant 設定](guide:window.settingsHome#voice-assistant)

### 建立助理 {#create-pipeline}

`POST /api/phoenix-mcp/admin/voice_agent/pipeline`

| 欄位 | 類型 | 說明 |
|---|---|---|
| `preferred` | boolean | 選填。預設 `true`。使助理成為 Home Assistant 的慣用助理。 |

請先完整設定語音智慧體：已啟用，並有存取金鑰、提供者和模型。如果 Phoenix 的助理仍然存在，Phoenix 會復用它。

回應包含 `pipeline_id`、`name` 和 `preferred`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.booleanRequired` | `preferred` 不是布林值。 |
| 404 | `not_found` | `adminError.setupMissing` | Phoenix 尚未設定。 |
| 400 | `invalid_request` | `adminError.assistPipelineUnavailable` | Home Assistant 沒有助理支援。 |
| 400 | `invalid_request` | `adminError.voiceAgentNotConfigured` | 語音智慧體尚未完全設定並開啟。 |
| 400 | `invalid_request` | `adminError.assistPipelineFailed` | 建立助理失敗。 |

### 移除助理 {#remove-pipeline}

`DELETE /api/phoenix-mcp/admin/voice_agent/pipeline` 會在需要時把慣用權移到另一個助理，然後刪除 Phoenix 的助理。回應為 `{"ok": true, "kept": false}`。

當 Home Assistant 仍保留該助理時，`kept` 為 `true`，因為它已經是唯一助理，或刪除失敗。

### 慣用助理 {#preferred-pipeline}

`POST` 讓 Phoenix 的助理成為慣用，`DELETE` 停止它作為慣用但保留它。兩者都不接受本文。回應包含 `pipeline_id` 和 `preferred`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.assistPipelineUnavailable` | Home Assistant 沒有助理支援。 |
| 400 | `invalid_request` | `adminError.assistantSetupFirst` | 還沒有 Phoenix 助理。 |
| 400 | `invalid_request` | `adminError.assistantOnlyOneStaysPreferred` | Phoenix 的是唯一助理，因此它保持慣用。 |
| 400 | `invalid_request` | `adminError.assistantPreferredFailed` | 儲存慣用設定失敗。 |

## AI 任務預設 {#ai-task}

```
GET    /api/phoenix-mcp/admin/ai_task/preferred
POST   /api/phoenix-mcp/admin/ai_task/preferred
DELETE /api/phoenix-mcp/admin/ai_task/preferred
```

讀取和設定 Home Assistant 的預設資料產生實體。`POST` 讓 Phoenix 的 AI 任務實體成為預設，替換其他任何實體。`DELETE` 僅在預設實體是 Phoenix 的時候清除它。

三者都傳回相同欄位：

| 欄位 | 說明 |
|---|---|
| `supported` | Home Assistant 是否支援 AI 任務。 |
| `entity_id` | Phoenix 的 AI 任務實體，或在 AI 任務完全設定好之前為 `null`。 |
| `gen_data_entity_id` | 目前預設實體。 |
| `gen_data_name` | 目前預設實體的名稱。 |
| `is_preferred` | Phoenix 的實體是否為預設。 |

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.setupMissing` | Phoenix 尚未設定。 |
| 400 | `invalid_request` | `adminError.aiTaskNotConfigured` | 僅限 `POST`。Phoenix 的 AI 任務尚未完全設定並開啟。 |
| 400 | `invalid_request` | `adminError.aiTaskPreferencesUnavailable` | 僅限 `POST`。Home Assistant 沒有儲存預設值的位置。 |
| 400 | `invalid_request` | `adminError.aiTaskPreferredFailed` | 僅限 `POST`。儲存預設值失敗。 |

相關：[Home Assistant 設定](guide:window.settingsHome#ai-task-default)

## 命令路由 {#routing}

```
GET    /api/phoenix-mcp/admin/command-routing/preview
POST   /api/phoenix-mcp/admin/command-routing/preview
GET    /api/phoenix-mcp/admin/command-routing/statistics
DELETE /api/phoenix-mcp/admin/command-routing/statistics
```

測試 Phoenix 會如何路由一個句子，並讀取路由計數和總量。

相關：[命令如何路由](guide:topic.commandRouting)、[閱讀路由統計](guide:topic.routingStatistics)

### 即時計數器 {#routing-counters}

`GET /api/phoenix-mcp/admin/command-routing/preview` 傳回儲存在記憶體中的計數器。Phoenix 重新啟動時它們從零開始。

| 欄位 | 說明 |
|---|---|
| `observations`、`candidates`、`reasons`、`mean_elapsed_ms`、`agent_chat` | 語音辨識計數器。 |
| `decision_shadow` | 決策計數器：`observations`、`reasons`、`choices`、`busy`、`requests`、`tool_routing`、`mean_elapsed_ms`、`hybrid`、`chat_hybrid` 和 `agent_chat`。 |
| `request_budget` | `limit`、`used`、`remaining`（不限時為 `null`）、`reset_at` 和 `available`。`reset_at` 為 Unix 秒，位於下一個 UTC 小時的開始。 |

### 測試句子 {#routing-preview}

`POST /api/phoenix-mcp/admin/command-routing/preview` 顯示 Phoenix 會如何路由一個句子，而不執行它。它不計入觀察。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `access_key_id` | string | 必填。一個有效存取金鑰。 |
| `sentence` | string | 必填。不能為空，最多 512 個字元。 |
| `language` | string | 預設 `en`。任何已安裝的 Assist 語言，包括地區和中文文字變體。 |
| `decision` | boolean | 還會詢問決策提供者。 |
| `targets` | boolean | 需要 `decision`。 |
| `batch` | boolean | 需要 `decision` 和 `targets`，且不能有 `messages`。 |
| `numeric` | boolean | 需要 `batch`。 |
| `messages` | list | 更早的輪次，用於歷史審核。需要 `decision`，且不能有 `targets`。 |
| `references` | boolean | 需要 `messages`。 |

不允許其他欄位。任何需要 `decision` 的選項都會聯絡決策帳戶，並計入 `decision_request_budget`。

回應包含 `route`（`local_candidate` 或 `provider`）、`reason`、`proposal`、`executed: false`、`elapsed_ms`、`voice_observations` 和 `decision_observations`。依選項，它還可能包含 `decision_shadow`、`batch_review` 或 `context_review`。

如果測試期間金鑰或設定發生變化，結果為 `route: provider`，`reason: authority_changed`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.routingPreviewInvalid` | 某個欄位缺少、過長、類型錯誤、不允許，或組合不允許。 |
| 400 | `invalid_request` | `adminError.activeAccessKeyRequired` | `access_key_id` 指定的不是有效金鑰。`message_params` 包含 `field`。 |

### 路由統計 {#routing-statistics}

`GET /api/phoenix-mcp/admin/command-routing/statistics` 傳回已儲存的總量，跨重新啟動保留。回應包含 `voice` 和 `chat`，各含：

- `since`
- `outcomes`：`local`、`decision`、`provider`、`pending`、`failed` 和 `cancelled`
- `provider_turns`
- `decision_usage` 和 `provider_usage`，各含 `requests`、`reports`、`input_tokens` 和 `output_tokens`

### 重設統計 {#reset-statistics}

`DELETE /api/phoenix-mcp/admin/command-routing/statistics` 重設一個介面的總量：

```json
{"surface": "voice", "confirm": true}
```

把 `surface` 設為 `voice` 或 `chat`。回應會顯示重設後的兩個介面。決策請求計數不會被重設。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.routingStatisticsResetInvalid` | 本文不完全是有效的 `surface` 加上 `confirm: true`。 |
