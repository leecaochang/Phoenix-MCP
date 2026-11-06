---
{ "id": "topic.agentEndpoints", "title": "智慧體端點", "parent": "topic.adminApi", "order": 60, "keywords": ["MCP 端點", "JSON-RPC", "智慧體 API", "phx_", "Streamable HTTP"], "sectionKeywords": { "routes": ["405", "尾隨斜線", "緊急停止開關"], "bearer": ["Authorization", "Bearer", "401", "WWW-Authenticate", "未授權", "建議"], "origin": ["Origin", "瀏覽器", "403", "CORS"], "mcp": ["POST", "JSON-RPC", "批次", "-32700", "-32600", "-32601", "SSE", "text/event-stream", "協定版本", "2026-07-28", "2025-03-26"], "sessions": ["Mcp-Session-Id", "舊版", "工作階段限制", "initialize"], "end-session": ["DELETE", "關閉工作階段"], "context": ["內容文件", "format=json", "實體"], "skill": ["SKILL.md", "技能指南", "Markdown"], "rate-limit": ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After", "429", "rate_limited", "突發"] } }
---
智慧體使用 Phoenix 存取金鑰（`phx_...`）透過這些路由存取 Phoenix，而不是 Home Assistant 權杖。本頁面向建置或檢查 MCP 用戶端的開發者。

相關：[連線外部應用程式](guide:topic.connecting)、[存取金鑰](guide:topic.security#keys)

## 路由 {#routes}

```
POST   /api/phoenix-mcp
DELETE /api/phoenix-mcp
GET    /api/phoenix-mcp/context
GET    /api/phoenix-mcp/skill
```

智慧體向第一個路由傳送 MCP 請求，從第二個路由讀取其金鑰的內容，並從最後一個路由取得技能指南。

- POST 和 DELETE 也支援帶尾隨斜線的 `/api/phoenix-mcp/`。
- `GET /api/phoenix-mcp` 傳回 `405`，因為該端點沒有 GET 處理常式。
- 緊急停止開關開啟時，這些路由不會服務智慧體。

## 傳送金鑰 {#bearer}

在 `Authorization` 標頭中把金鑰作為 `Bearer phx_...` 傳送。金鑰是 `phx_` 後接 64 個字元。絕不要把它放在 URL 中：即使標頭正確，`token` 或 `access_token` 查詢參數也會被拒絕。

除技能外的每個路由都會依此順序檢查金鑰：

| 狀態 | `error` | 條件 |
|---|---|---|
| 503 | `service_unavailable` | Phoenix 正在啟動或關閉。 |
| 503 | `service_unavailable` | 緊急停止開關已開啟。 |
| 401 | `unauthorized` | URL 帶有 `token` 或 `access_token` 查詢參數。 |
| 401 | `unauthorized` | `Authorization` 標頭不以 `Bearer ` 開頭。 |
| 401 | `unauthorized` | 值不是 `phx_` 後接 64 個字元。 |
| 401 | `unauthorized` | 沒有金鑰具有此值。 |
| 401 | `unauthorized` | 金鑰已被撤銷或已過期。已過期的金鑰會移到歸檔。 |
| 429 | `rate_limited` | 金鑰超過其速率限制。參見[速率限制](guide:#rate-limit)。 |

每個 `401` 都帶有 `WWW-Authenticate: Bearer realm="Phoenix MCP"`。通過檢查的金鑰會在速率限制檢查前更新其 `last_used_at`。

這些路由上的錯誤本文比管理 API 更簡單：`error` 和 `message`，有時帶一個 `suggestions` 陣列。它們帶有 `X-Phoenix-Request-ID`，但沒有 `message_key`，也沒有 `Cache-Control`。

```json
{ "error": "unauthorized", "message": "Unauthorized." }
```

相關：[錯誤](guide:topic.adminApi#errors)

## 瀏覽器來源 {#origin}

MCP 端點在 POST 和 DELETE 上都會拒絕來自其他網站網頁的請求。`Origin` 標頭不是 Home Assistant 自己的 URL 之一的請求會傳回 `403`、`forbidden` 和訊息 "Origin not allowed."。Phoenix 會在檢查金鑰之前檢查這一點。

桌面和命令列應用程式不傳送 `Origin`，因此不受影響。內容路由不檢查來源。

## MCP 端點 {#mcp}

`POST /api/phoenix-mcp` 接受一條 JSON-RPC 訊息，或一個批次，最大 1 MiB。Phoenix 支援協定修訂 `2026-07-28` 和 `2025-03-26`。修訂 `2025-03-26` 在下文稱為舊版。

| 狀態 | 本文 | 條件 |
|---|---|---|
| 200 | JSON-RPC 結果 | 請求成功。 |
| 202 | 無 | 訊息是通知。 |
| 400 | JSON-RPC 錯誤 `-32700` | 本文為空或不是有效 JSON。 |
| 400 | JSON-RPC 錯誤 `-32600` | 本文不是 JSON 物件或批次。 |
| 200 | JSON-RPC 錯誤 `-32601` | 舊版：方法不存在。 |
| 404 | 錯誤 | `2026-07-28`：方法不存在。 |
| 400 | JSON-RPC 錯誤 `-32600` | `2026-07-28` 下的批次，過大或為空的舊版批次，或不含請求且有訊息被拒絕的舊版批次。 |
| 413 | `request_too_large` | 本文超過 1 MiB。 |
| 400 | `invalid_request` | 無法讀取本文，或 30 秒後本文仍未傳完。 |
| 403 | `forbidden` | `Origin` 不被允許。 |
| 503 | `service_unavailable` | Phoenix 尚未就緒。 |

與請求不符合的協定標頭會傳回 JSON-RPC 錯誤 `-32020`。不支援的協定版本會傳回 `-32022`。

若要以串流方式取得回應，請傳送 `Accept: text/event-stream`。Phoenix 會把單一回應作為一條 SSE `message` 事件傳送，並在工作時每 15 秒傳送保活。

### 批次 {#batches}

只有舊版請求可以是批次。當批次超過 50 項、超過金鑰的突發上限，或超過其每分鐘限制時，Phoenix 會以 `400` 和 `-32600` 拒絕它。

Phoenix 在執行前會按整個批次計入速率限制。當剩餘限制太少時，批次會傳回 `429`、`rate_limited` 和 `Retry-After`，且其中沒有任何內容執行。

### 舊版工作階段 {#sessions}

在舊版修訂下，`initialize` 傳回 `Mcp-Session-Id` 標頭。在之後每個請求中傳送它。

- Phoenix 總共最多保留 256 個工作階段，每個金鑰最多 8 個。
- 工作階段在無使用一小時後關閉；如果從未完成初始化，則在 60 秒後關閉。
- 達到工作階段限制時，Phoenix 會關閉該金鑰最近最少使用的閒置工作階段。
- 當沒有閒置工作階段時，請求傳回 `429`、JSON-RPC 錯誤 `-32000` 和 `Retry-After`。
- 未知的 `Mcp-Session-Id` 傳回 `404` 和 `-32000`。缺少時傳回 `400`。

當金鑰自己的工作階段都忙時，`Retry-After` 為 5 秒。否則它是直到最舊工作階段過期的時間，至少 5 秒。

### 結束工作階段 {#end-session}

`DELETE /api/phoenix-mcp` 關閉舊版工作階段。它執行與 POST 相同的就緒、來源和金鑰檢查，包括速率限制。回應為 `204`，沒有本文。

| 狀態 | `error` | 條件 |
|---|---|---|
| 400 | `invalid_request` | 缺少 `Mcp-Session-Id` 標頭。 |
| 404 | `not_found` | 工作階段不存在、已過期或屬於另一個金鑰。 |

## 內容 {#context}

`GET /api/phoenix-mcp/context` 描述金鑰可以存取什麼。預設以純文字傳回；新增 `?format=json` 取得 JSON。它會執行金鑰檢查和速率限制，每次呼叫都會進入稽核日誌。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `access_key_name` | string | |
| `pass_through` | boolean | |
| `persona` | string | |
| `notes` | string 或 null | 金鑰的備註。 |
| `entities` | array | 每個有 `entity_id`、`permission`（`READ` 或 `READ/WRITE`）、`area_id`，以及設定後的 `hint`。 |
| `capability_flags` | object | 適用於智慧體的能力值。 |
| `rate_limit` | object | `requests_per_minute` 和 `burst_per_second`。 |

Home Companion 金鑰只會收到 `access_key_name`、`persona`、空的 `entities`、`capability_flags` 和 `message`。

## 技能 {#skill}

`GET /api/phoenix-mcp/skill` 以 Markdown（`text/markdown`）傳回 Phoenix MCP 技能指南。它不需要金鑰，不受速率限制，也不包含關於你家的任何內容。它對所有人都是相同的。

Phoenix 正在啟動或關閉，或緊急停止開關開啟時，它傳回 `503` 和純文字 "Service unavailable."。其回應不帶有 `X-Phoenix-Request-ID`。

相關：[智慧體會收到什麼](guide:topic.connecting#primer)

## 速率限制 {#rate-limit}

每個金鑰有兩個限制。`rate_limit_requests` 統計滑動 60 秒內的請求。`rate_limit_burst` 統計任意一秒內的請求；0 會關閉突發限制。`rate_limit_requests` 為 0 會同時關閉兩者。

相關：[{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting)

### 限制標頭 {#limit-headers}

成功的 MCP 回應會告訴智慧體它的狀況：

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 47
X-RateLimit-Reset: 1793612345
```

| 標頭 | 含義 |
|---|---|
| `X-RateLimit-Limit` | 金鑰的 `rate_limit_requests`。 |
| `X-RateLimit-Remaining` | 目前 60 秒內，在本次請求之後剩餘的請求數。批次會計入每一項。 |
| `X-RateLimit-Reset` | Unix 時間，以整秒為單位，表示時間範圍中最舊的請求離開的時間。 |

這些標頭只出現在 `POST /api/phoenix-mcp` 的 `200` JSON 和 SSE 回應中，包括批次。通知、協定錯誤、其他錯誤、DELETE、內容和技能不會收到它們。關閉速率限制的金鑰不會收到任何標頭。

### 達到限制時 {#limit-hit}

請求傳回 `429`、`rate_limited` 和一個以秒為單位的 `Retry-After` 標頭，至少為 1。它是直到足夠多的舊請求離開每分鐘或突發時間範圍的時間。`429` 不帶有 `X-RateLimit` 標頭。

```json
{ "error": "rate_limited", "message": "Rate limit exceeded." }
```

在重試前等待 `Retry-After`。

相關：[金鑰統計](guide:topic.adminApiKeys#key-stats)
