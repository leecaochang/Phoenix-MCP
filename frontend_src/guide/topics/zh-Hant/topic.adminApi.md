---
{ "id": "topic.adminApi", "title": "管理 API", "parent": "topic.about", "order": 60, "keywords": ["API", "REST", "開發者", "端點", "整合", "腳本", "curl"], "sectionKeywords": { "auth": ["身分驗證", "長期存取權杖", "401", "403", "管理員", "Bearer"], "responses": ["信封", "X-Phoenix-Request-ID", "Cache-Control", "204"], "errors": ["錯誤代碼", "message_key", "message_params", "message_passthrough", "狀態代碼"], "bodies": ["413", "請求體", "JSON", "分頁", "limit", "offset"], "ids": ["節點 ID", "實體 ID", "域"], "states": ["GREY", "YELLOW", "GREEN", "RED", "權限狀態"] } }
---
面板建置在 Phoenix 的管理 API 之上，你也可以使用它：從腳本、自動化工具或你自己的應用程式。面板能做的一切，API 都能做。這些頁面面向開發者。

相關：[存取金鑰端點](guide:topic.adminApiKeys)、[核准與變更端點](guide:topic.adminApiApprovals)、[MESA 端點](guide:topic.adminApiMesa)、[Agent Chat 端點](guide:topic.adminApiChat)、[設定與系統端點](guide:topic.adminApiSettings)、[智慧體端點](guide:topic.agentEndpoints)

## 登入 {#auth}

每個管理端點都位於 `/api/phoenix-mcp/admin` 下，並且需要 Home Assistant 管理員。傳送任何管理員的權杖，例如長期存取權杖，作為 `Authorization: Bearer <token>`。

- 沒有有效權杖時，Home Assistant 本身會在 Phoenix 執行前傳回 `401`，沒有 JSON 本文。
- 已登入但不是管理員的使用者會收到 `403`，錯誤為 `forbidden`。
- Phoenix 仍在啟動時，除目錄外的每個端點都會傳回 `503`，錯誤為 `service_unavailable`。
- Phoenix 存取金鑰（`phx_...`）無法開啟這些端點。它們僅供智慧體使用。

緊急停止開關開啟時，管理 API 仍會運作。

相關：[管理員這一側](guide:topic.security#admin)

## 回應 {#responses}

成功的呼叫會傳回 `200` 或 `201` 和 JSON 本文，或傳回 `204` 且沒有本文。每個回應都帶有 `X-Phoenix-Request-ID`，稽核日誌和 Home Assistant 的日誌也會記錄它。帶本文的回應還會新增 `Cache-Control: no-store`。

你透過 API 所做的每項變更都會以發起變更的管理員身分向稽核日誌新增一筆條目。讀取不會被記錄。

## 錯誤 {#errors}

錯誤會傳回狀態代碼和此本文：

```json
{
  "error": "invalid_request",
  "message": "English description of the problem.",
  "message_key": "adminError.badJson",
  "message_params": {}
}
```

- `error` 是下面代碼之一。請針對它撰寫程式碼，而不是針對訊息。
- `message_key` 指定面板對訊息的翻譯，`message_params` 填入它。`message_params` 僅在訊息有值時出現。
- 來自 Phoenix 外部的文字，例如 AI 提供者自己的錯誤，會帶有 `message_passthrough: true`，而不是鍵。

| 代碼 | 通常狀態 | 含義 |
|---|---|---|
| `invalid_request` | 400 | 請求格式錯誤，或某個值超出範圍。 |
| `unauthorized` | 401 | 未登入。通常由 Home Assistant 先傳回，如上所述。如果由 Phoenix 傳回，`message_key` 為 `adminError.authRequired`。 |
| `forbidden` | 403 | 已登入，但不是管理員。 |
| `not_found` | 404 | 項目不存在。 |
| `conflict` | 409 | 項目的目前狀態不允許此操作，例如名稱已被使用。 |
| `already_exists` | 409 | 項目已存在。 |
| `stale_version` | 409 | 另一項變更改先到達。請重新讀取並重試。 |
| `restore_unverified` | 409 | 已傳送還原，但結果不符合。重試前請檢查。 |
| `precondition_failed` | 412 | 缺少必要的確認或條件。 |
| `request_too_large` | 413 | 本文超過 1 MiB。 |
| `internal_error` | 500 | Phoenix 內部有內容失敗。 |
| `service_unavailable` | 503 | Phoenix 正在啟動，或該功能已關閉。 |

## 請求體 {#bodies}

傳送最大 1 MiB 的 JSON 物件。空本文視為 `{}`。無論 Content-Type 是什麼，Phoenix 都會把本文按 JSON 讀取。

| 問題 | 狀態 | `message_key` |
|---|---|---|
| 本文超過 1 MiB | 413 | `adminError.bodyTooLarge` |
| 本文無法讀取，或 2 分鐘後仍未傳完 | 400 | `adminError.bodyUnreadable` |
| 不是有效 JSON | 400 | `adminError.badJson` |
| JSON 不是物件 | 400 | `adminError.bodyNotObject` |

分頁清單接受 `limit` 和 `offset`，兩者都是大於等於 0 的整數。每個端點都會限制 `limit`；錯誤的值會傳回 `400` 和 `adminError.badPagination` 或 `adminError.paginationNonNegative`。

## 路徑中的 ID {#ids}

- 域是小寫字母、數字和底線，以字母開頭，例如 `light`。
- 實體 ID 是域、一個點，以及小寫字母、數字和底線，例如 `light.kitchen`。
- 裝置 ID 是 Home Assistant 的裝置登錄檔 ID。
- 任何 ID 最多 255 個字元，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;`。

## 權限狀態 {#states}

API 對四種權限狀態的命名與面板不同：

| API | 面板 | 含義 |
|---|---|---|
| `GREY` | N | 沒有自己的設定；沿用上一層。 |
| `YELLOW` | R | 唯讀。 |
| `GREEN` | W | 讀取和控制。 |
| `RED` | D | 拒絕存取。 |

相關：[權限如何判定](guide:topic.permissionResolution)
