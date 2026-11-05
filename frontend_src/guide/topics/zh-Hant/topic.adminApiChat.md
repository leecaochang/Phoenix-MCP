---
{ "id": "topic.adminApiChat", "title": "Agent Chat 端點", "parent": "topic.adminApi", "order": 40, "keywords": ["提供者", "AI 提供者", "帳戶", "模型", "聊天", "智慧體輪次", "SSE", "串流"], "sectionKeywords": { "providers": ["提供者帳戶", "執行個體", "instance_id"], "provider-kinds": ["Anthropic", "OpenAI", "Claude", "ChatGPT", "Gemini", "Ollama", "OpenRouter", "Qwen", "TypeSafe", "JEV", "決策提供者", "endpoint_id", "區域"], "setup-fields": ["api_key", "base_url", "endpoint_id", "API 金鑰"], "list-providers": ["provider_types", "purpose", "capabilities"], "change-provider": ["自動模型", "auto", "model_mode"], "refresh-models": ["重新整理", "能力"], "probe-model": ["探測", "選項", "努力", "視覺"], "check-setup": ["探測", "測試連線"], "chat": ["agentcli/chat", "輪次", "access_key_id"], "chat-options": ["思考", "努力", "溫度", "max_tokens", "show_thinking"], "chat-images": ["附件", "影像", "JPEG", "PNG", "GIF", "base64"], "events": ["保活", "text/event-stream", "Server-Sent Events", "ready", "status", "thinking_delta", "assistant_delta", "focus_declined", "tool_call", "tool_progress", "approval_required", "approval_resolved", "tool_image", "tool_result", "usage", "notice", "continue_required", "error", "messages", "done", "事件串流", "SSE"] } }
---
使用這些端點管理 Phoenix 使用的 AI 提供者帳戶，並從你自己的程式碼執行 Agent Chat 輪次。每個端點都遵循[管理 API](guide:topic.adminApi)概述中的規則。

相關：[{{label:agentchat.title}}](guide:window.agentChat)、[AI 提供者設定](guide:window.settingsProviders)、[錯誤](guide:topic.adminApi#errors)

## 提供者帳戶 {#providers}

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

新增、檢查和移除 Phoenix 用來存取 AI 提供者的帳戶。對話帳戶服務 Agent Chat、語音智慧體和 AI 任務。決策帳戶服務決策提供者。

### 提供者類型 {#provider-kinds}

每個帳戶都有一個 `kind`。帶端點的類型接受 `endpoint_id`；列出的第一個值是預設值。

| 類型 | 用途 | 設定欄位 |
|---|---|---|
| `claude`（Anthropic） | 對話 | `api_key` |
| `deepseek` | 對話 | `api_key` |
| `chatgpt`（OpenAI） | 對話 | `api_key`、`endpoint_id`：`global`、`us`、`eu`、`ae`、`au`、`ca`、`jp`、`in`、`sg`、`kr`、`gb` |
| `gemini` | 對話 | `api_key` |
| `grok` | 對話 | `api_key`、`endpoint_id`：`global`、`us` |
| `groq` | 對話 | `api_key` |
| `kimi` | 對話 | `api_key`、`endpoint_id`：`global`、`china` |
| `meta` | 對話 | `api_key` |
| `minimax` | 對話 | `api_key`、`endpoint_id`：`global`、`china` |
| `mistral` | 對話 | `api_key`、`endpoint_id`：`global`、`eu`、`us` |
| `nvidia` | 對話 | `api_key` |
| `mimo`（Xiaomi MiMo） | 對話 | `api_key`、`endpoint_id`：`payg`、`token_plan`、`token_plan_sgp`、`token_plan_ams` |
| `tencent`（Tencent Cloud TokenHub） | 對話 | `api_key`、`endpoint_id`：`singapore`、`guangzhou`、`silicon_valley` |
| `opencode` | 對話 | `api_key`、`endpoint_id`：`zen`、`console` |
| `ollama_cloud` | 對話 | `api_key` |
| `ollama`（本機） | 對話 | `base_url` |
| `openrouter` | 對話 | `api_key` |
| `together` | 對話 | `api_key` |
| `cerebras` | 對話 | `api_key` |
| `fireworks` | 對話 | `api_key` |
| `qwen` | 對話 | `api_key`、`base_url` |
| `zai` | 對話 | `api_key`、`endpoint_id`：`standard`、`coding`、`china` |
| `typesafe`（TypeSafe） | 決策 | `api_key`、`model` |

### 設定欄位 {#setup-fields}

新增帳戶和檢查設定接受相同的本文。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `kind` | string | 必填。上方的類型之一。 |
| `api_key` | string | 除 `ollama` 外每種類型都必填。最多 4096 個字元，不能包含控制字元。Phoenix 絕不傳回它。 |
| `base_url` | string | `ollama` 和 `qwen` 必填。只有 `ollama` 可以使用私有位址。其他每種類型都使用固定端點，並忽略此欄位。 |
| `endpoint_id` | string | 僅用於帶端點的類型。省略它表示預設值。 |
| `model` | string | 對話類型選填；留空表示自動跟隨提供者。`typesafe` 必填：最多 200 個字元，且必須在提供者目前清單中。 |

### 列出帳戶 {#list-providers}

`GET /api/phoenix-mcp/admin/agentcli/providers`

| 欄位 | 類型 | 說明 |
|---|---|---|
| `purpose` | query string | `conversation`（預設）或 `decision`。其他任何值傳回空清單，而不是錯誤。 |

回應包含 `instances`（帳戶）和 `provider_types`（設定表單）。兩者都依標籤排序。

`provider_types` 中的每個條目有 `kind`、`label`、`label_key` 和 `fields`。每個欄位有 `id`、`type`（`secret`、`url` 或 `choice`）、`label_key` 和 `required`。它還可能包含 `placeholder` 和 `choices`，每個選擇有 `value`、`label` 和 `label_key`。

`instances` 中的每個帳戶有這些欄位。API 金鑰絕不在其中。

| 欄位 | 說明 |
|---|---|
| `id` | 帳戶的 ID，用作 `{instance_id}`。 |
| `kind` | 提供者類型。 |
| `name` | 由 Phoenix 產生。僅當一種類型有多個帳戶時，它才會新增區分部分。 |
| `model` | 請求現在使用的模型。 |
| `model_mode` | `fixed` 或 `auto`。決策帳戶一律為 `fixed`。 |
| `model_replaced_from` | 提供者已停用的所選模型，或 `null`。 |
| `models_checked_at` | Phoenix 上次讀取模型清單的時間。 |
| `base_url` | 按設定時的值。 |
| `endpoint_id` | 按設定時的值。 |
| `capabilities` | 依模型：提供者宣告的、探測發現的，以及提供者在過去 30 天拒絕的選項。 |
| `capabilities_checked_at` | Phoenix 上次檢查能力的時間。 |

### 新增帳戶 {#add-provider}

`POST /api/phoenix-mcp/admin/agentcli/providers` 接受[設定欄位](guide:#setup-fields)。

對於對話類型，Phoenix 會執行提供者的憑據檢查。對於決策類型，它會載入模型清單並要求你的模型在其中。如果通過，Phoenix 會儲存帳戶，並嘗試讀取其模型清單和宣告的能力。

回應為 `201`，包含 `{"instance": {...}}`，帶有上方的帳戶欄位。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.providerUnknown` | `kind` 不是已知類型。 |
| 400 | `invalid_request` | `adminError.providerApiKeyRequired` | 缺少 `api_key`。 |
| 400 | `invalid_request` | `adminError.providerEndpointInvalid` | `endpoint_id` 不是該類型的值之一。 |
| 400 | `invalid_request` | `adminError.ollamaUrlRequired`、`adminError.providerBaseUrlRequired` | 缺少必要的 `base_url`。 |
| 400 | `invalid_request` | `adminError.providerUrlInvalid`、`adminError.providerUrlScheme`、`adminError.providerRemoteUrlScheme`、`adminError.providerUrlHostRequired`、`adminError.providerUrlCredentialsForbidden`、`adminError.providerUrlFragmentForbidden`、`adminError.providerUrlAddressForbidden`、`adminError.providerUrlResolutionForbidden` | `base_url` 不是可接受的 URL。 |
| 400 | `invalid_request` | `adminError.providerConnectionFailed`，或提供者自身失敗的鍵 | 憑據檢查失敗。`adminError.providerConnectionFailed` 會帶有 `detail`。 |
| 400 | `invalid_request` | `adminError.providerModelRequired` | 決策類型沒有 `model`。 |
| 400 | `invalid_request` | `adminError.providerModelNotListed` | 模型不在提供者的清單中。 |
| 400 | `invalid_request` | `adminError.decisionProviderRateLimited`、`adminError.decisionProviderTimeout`、`adminError.decisionProviderUnreachable`、`adminError.decisionProviderUnreadable`、`adminError.providerKeyRejected`、`adminError.decisionProviderCheckFailed` | 決策提供者檢查失敗：速率受限、逾時、無法連線、無法讀取、金鑰被拒絕或其他失敗。 |
| 409 | `already_exists` | `adminError.providerAlreadyConfigured` | 已有相同設定的帳戶存在。 |

### 變更模型 {#change-provider}

`PATCH /api/phoenix-mcp/admin/agentcli/providers/{instance_id}`

| 欄位 | 類型 | 說明 |
|---|---|---|
| `model` | string | 要使用的模型。對話帳戶接受任何模型 ID。決策帳戶需要提供者目前列出的模型。 |
| `auto` | boolean | `true` 會自動跟隨提供者的模型。決策帳戶拒絕此項。 |

傳送 `model` 或 `auto: true`，不要同時傳送，也不要傳送其他欄位。使用 `auto: true` 時，Phoenix 會立即讀取模型清單，因此回應會顯示它選擇的模型。

回應為 `200`，僅包含 `{"instance": {"id": ..., "model": ..., "model_mode": ...}}`，而不是完整帳戶。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.unknownFields` | 本文包含 `model` 和 `auto` 以外的欄位。 |
| 400 | `invalid_request` | `adminError.booleanRequired` | `auto` 不是布林值。 |
| 400 | `invalid_request` | `adminError.providerModelOrAuto` | `model` 和 `auto: true` 同時傳送。 |
| 400 | `invalid_request` | `adminError.providerModelRequired` | 兩者都沒有傳送。 |
| 400 | `invalid_request` | `adminError.decisionModelRequired` | `auto: true` 傳給了決策帳戶，而決策帳戶需要具體模型。 |
| 400 | `invalid_request` | `adminError.providerModelNotListed` 以及[新增帳戶](guide:#add-provider)下的決策提供者錯誤 | 決策帳戶的模型未列出，或檢查失敗。 |
| 409 | `conflict` | `adminError.providerAccountChanged` | 決策帳戶在檢查期間發生了變化。請重新讀取並重試。 |
| 404 | `not_found` | `adminError.providerAccountNotFound` | 沒有帳戶具有此 ID。 |

### 移除帳戶 {#remove-provider}

`DELETE /api/phoenix-mcp/admin/agentcli/providers/{instance_id}` 不接受本文。它會移除帳戶並清除所有指向它的設定：

- 語音智慧體的提供者和模型。
- AI 任務的提供者和模型，以及其實體和 Phoenix Assist 管線。
- 對於正在使用的決策帳戶：`decision_provider_id`、`decision_shadow_enabled`、全部三種工具審核模式，以及任何 `hybrid` 路由模式（會變為 `off`）。

如果你移除預設決策帳戶，另一個決策帳戶會成為預設。回應為 `200`，包含 `{"deleted": "<instance_id>"}`，即使 ID 不存在也是如此。

相關：[設定與系統端點](guide:topic.adminApiSettings#settings)

### 列出模型 {#list-models}

`GET /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models` 向提供者請求一次其模型。回應為 `{"models": [...]}`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured` | 沒有帳戶具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerConnectionFailedNoDetail` | 無法連線決策提供者。 |

### 重新整理模型和能力 {#refresh-models}

`POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh` 不接受本文。它會重新讀取模型清單和宣告的能力並儲存它們。它不傳送補全請求，因此不消耗 token。對於 `ollama`，它會為每個模型傳送一個請求。

回應包含 `models`、`capabilities`、`declared`（提供者是否回報能力）和 `checked_at`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured`、`adminError.providerAccountNotFound` | 沒有帳戶具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerConnectionFailedNoDetail` | 無法連線決策提供者。 |

### 檢查模型選項 {#probe-model}

`POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe` 不接受本文。它會詢問提供者所選模型接受哪些選項。這會消耗額度。

Phoenix 最多傳送 10 個單一 token 補全，並在影像支援未知時額外傳送一次影像檢查。結果會覆蓋宣告的能力。

回應包含 `model`、`probed`、`calls`、`checked_at`、`effort_checkable`、`answered` 和 `vision_checked`。當提供者拒絕了每個問題時，`answered` 為 `false`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured`、`adminError.providerAccountNotFound` | 沒有帳戶具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerChooseModel` | 帳戶沒有選擇模型。 |

### 檢查設定 {#check-setup}

`POST /api/phoenix-mcp/admin/agentcli/probe` 接受[設定欄位](guide:#setup-fields)，檢查它們並列出模型。它不儲存任何內容。

設定或連線失敗仍會傳回 `200`，失敗內容在本文中：

```json
{ "ok": false, "error": "English description.", "message_key": "adminError.providerConnectionFailed", "message_params": {}, "models": [] }
```

成功時本文為 `{"ok": true, "models": [...]}`。當文字來自提供者時，失敗會帶有 `message_passthrough: true` 而不是 `message_key`。`message_params` 僅在訊息有值時出現。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.providerUnknown` | `kind` 不是已知類型。 |
| 409 | `already_exists` | `adminError.providerAlreadyConfigured` | 已有相同設定的帳戶存在。 |

## 執行聊天輪次 {#chat}

```
POST   /api/phoenix-mcp/agentcli/chat
```

執行一個 Agent Chat 輪次，並以 Server-Sent Events 串流傳回。注意路徑：它位於 `/admin` 之外，但仍需要管理員。

該路由遵循緊急停止開關。開關開啟時，它傳回 `503`。如果 Phoenix 啟動時開關已開啟，Home Assistant 會在你關閉開關前傳回 `404`。

相關：[緊急停止開關](guide:topic.killSwitch)

### 請求體 {#chat-body}

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

| 欄位 | 類型 | 說明 |
|---|---|---|
| `access_key_id` | string | 必填。智慧體工作所用的存取金鑰。它必須處於有效狀態。 |
| `instance_id` | string | 必填。要使用的提供者帳戶。 |
| `model` | string | 選填。覆蓋此輪次中帳戶的模型。 |
| `user` | string | 新訊息。除非傳送 `attachments` 或 `continue: true`，否則必填。 |
| `attachments` | list | 此訊息的影像。參見[影像](guide:#chat-images)。 |
| `messages` | list | 之前的輪次。Phoenix 不保留逐字稿：從 `messages` 事件中保留清單，並在下一輪次中傳回。 |
| `options` | object | 模型選項。參見[選項](guide:#chat-options)。 |
| `continue` | boolean | `true` 會恢復在輪次上限處暫停的輪次，不新增訊息。 |
| `language` | string | 命令路由使用的語言。面板一律傳送它。 |
| `log_conversation_id` | string | 在對話記錄中把輪次歸入一個執行緒。 |

輪次上限是 `agentcli_max_iterations` 設定。回覆風格、詳細級別和家庭專注模式在請求開始時來自 Agent Chat 設定。

相關：[Agent Chat 設定](guide:window.settingsChat)、[命令如何路由](guide:topic.commandRouting)

### 選項 {#chat-options}

Phoenix 只在提供者支援的地方使用每個選項。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `thinking` | boolean | 開啟模型的推理。預設 `true`。 |
| `effort` | string | `low`、`medium`、`high`、`xhigh` 或 `max`。預設 `high`。 |
| `show_thinking` | boolean | 是否顯示模型的推理。 |
| `temperature` | number | 提供者的取樣溫度。 |
| `max_tokens` | integer | 模型最多可以寫入的 token 數。預設 32000。 |

### 影像 {#chat-images}

每個附件為 `{"mime_type": "image/jpeg", "data": "<base64>"}`。

- 最多傳送 4 張影像，每張為 JPEG、PNG 或 GIF。
- 每張影像解碼後最多 2 MiB，每邊最多 8000 像素，所有影像合計最多 4 MiB。
- 影像自身的標頭必須與其 `mime_type` 符合。
- `messages` 中的影像共用同樣的 4 MiB 預算。超出會傳回 `413`。

此路由接受最大 6,706,518 位元組的本文。除影像外的一切仍必須適合 1 MiB。

### 串流開始前的錯誤 {#chat-errors}

Phoenix 依此順序檢查本文大小、設定、緊急停止開關、存取金鑰、帳戶、模型、影像和訊息。第一次失敗會以 HTTP 狀態傳回。[本文錯誤](guide:topic.adminApi#bodies)也適用。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 413 | `request_too_large` | `adminError.bodyTooLarge` | 本文或影像超出其限制。 |
| 404 | `not_found` | `adminError.setupMissing` | Phoenix 尚未設定。 |
| 503 | `service_unavailable` | `adminError.serviceDisabled` | 緊急停止開關已開啟。 |
| 503 | `service_unavailable` | `adminError.setupIncomplete` | Phoenix 仍在啟動。 |
| 503 | `service_unavailable` | `adminError.serviceStopping` | Home Assistant 正在關閉。 |
| 404 | `not_found` | `adminError.accessKeyNotFound` | 沒有存取金鑰具有此 ID。 |
| 400 | `invalid_request` | `adminError.accessKeyInactive` | 存取金鑰無效。 |
| 404 | `not_found` | `adminError.providerNotConfigured` | 沒有提供者帳戶具有此 ID。 |
| 400 | `invalid_request` | `adminError.providerModelMissing` | 帳戶沒有選擇模型。 |
| 400 | `invalid_request` | `adminError.attachmentsInvalid`、`adminError.attachmentsTooMany`、`adminError.attachmentTooLarge`、`adminError.attachmentUnsupported`、`adminError.attachmentDimensions`、`adminError.attachmentsTooLarge` | 影像無法讀取，或違反某條[影像限制](guide:#chat-images)。 |
| 400 | `invalid_request` | `adminError.attachmentsContinue` | 影像與 `continue: true` 一起傳送。影像只能隨新訊息傳送。 |
| 400 | `invalid_request` | `adminError.attachmentsCompanion` | 影像與 Home Companion 金鑰一起傳送，而它只接受文字。 |
| 400 | `invalid_request` | `adminError.attachmentsVisionUnsupported` | 模型不接受影像。 |
| 400 | `invalid_request` | `adminError.invalidHostHeader` | 請求的 `Host` 標頭無法構成 URL。 |
| 400 | `invalid_request` | `adminError.nothingToContinue` | 傳送了 `continue: true`，但沒有可繼續的對話。 |
| 400 | `invalid_request` | `adminError.emptyMessage` | 訊息為空，且沒有影像。 |

## 事件串流 {#events}

開始的輪次傳回 `200`，`Content-Type` 為 `text/event-stream`，並帶有 `X-Phoenix-Request-ID` 標頭。每個事件訊息包含一個事件名稱和一個 JSON 物件：

```
event: assistant_delta
data: {"text": "Turning off the kitchen lights."}
```

Phoenix 每 15 秒傳送一條 `: keepalive` 註解。串流開始後，問題會以 `error` 事件到達，絕不以 HTTP 狀態到達。標記為選填的欄位僅在它們有值時出現。

| 事件 | 資料欄位 | 說明 |
|---|---|---|
| `ready` | `provider`（類型）、`model`、`access_key_id` | 一律是第一個事件。 |
| `status` | `provider`（標籤）、`step`、`tools`、`phase`、`seconds`；重試期間還有 `attempt`、`detail`、`detail_code` | `phase` 為 `waiting`、`thinking` 或 `retrying`。在模型呼叫開始時、每次變化時，以及沒有其他內容到達時每 30 秒傳送。 |
| `thinking_delta` | `text` | 推理文字，僅在提供者串流傳送時。 |
| `assistant_delta` | `text` | 回覆文字。 |
| `focus_declined` | 無 | 在家庭專注模式下，模型拒絕了關於家之外的請求。Phoenix 會從回覆中移除模型的標記。 |
| `tool_call` | `id`、`name`、`arguments` | 在工具執行前傳送。 |
| `tool_progress` | `id`、`message`；選填 `message_key`、`message_params` | 長時間執行工具的即時狀態，最多每 2 秒一次。 |
| `approval_required` | `approval_id`、`tool_name`、`review_url`、`expires_at`、`diff` | 輪次最多等待 900 秒以取得決定。 |
| `approval_resolved` | `approval_id`、`status`、`reason`；選填 `reason_kind` | 當已核准的操作失敗時，`reason_kind` 為 `execution`。 |
| `tool_image` | `id`、`name`、`index`、`mime_type`、`data` | 來自工具結果的 base64 影像。 |
| `tool_result` | `id`、`name`、`is_error`、`summary` | `summary` 會縮短以供顯示。 |
| `usage` | `input_tokens`、`output_tokens`、`context_tokens`、`cache_read_tokens`、`cache_write_tokens` | 提供者到此輪次為止的總量，僅在回報用量時。 |
| `notice` | `code`、`message` | `code` 為 `bad_tool_call_retry`、`max_tokens_stop` 或 `empty_turn`。 |
| `continue_required` | `iterations` | 輪次在限制處暫停。使用 `continue: true` 傳送下一個請求。 |
| `error` | `code`、`message`；選填 `retryable`、`outcome`、`diagnostic`、`message_params`、`provider_detail`、`provider_code`、`rate_limit`、`refused` | `code` 包括 `cancelled`、`internal`、`kill_switch`、`unauthorized`、`review_changed`、`outcome_unknown`、`output_limit`、`invalid_request` 以及提供者自己的代碼。 |
| `messages` | `messages` | 完整更新後的逐字稿。保留它用於下一輪次。 |
| `done` | `stop_reason` | 一律是最後一個事件。失敗的輪次後，`stop_reason` 為 `error`。 |

相關：[核准與變更端點](guide:topic.adminApiApprovals)、[{{label:agentchat.title}}](guide:window.agentChat#approvals)
