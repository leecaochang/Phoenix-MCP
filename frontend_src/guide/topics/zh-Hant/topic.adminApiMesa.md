---
{ "id": "topic.adminApiMesa", "title": "MESA 端點", "parent": "topic.adminApi", "order": 30, "keywords": ["MESA API", "策略 API", "語意策略", "隱私分類", "安全策略"], "sectionKeywords": { "shared": ["mesaUnavailable", "503", "稽核日誌", "租約", "mesa_request_lease", "mesa_release_lease"], "entity-profiles": ["實體策略", "cursor", "next_cursor", "origin"], "list-profiles": ["篩選", "標籤", "分區", "inferred_ai", "has_more", "total_matched"], "read-profile": ["effective", "explanation", "resolved profile"], "save-profile": ["If-None-Match", "僅建立", "semantic_profile", "privacy_classification", "warnings", "mesaProfileExists"], "scope-profiles": ["裝置策略", "分區策略", "整合策略", "域策略"], "picker-lists": ["device-options", "integration-options"], "defaults": ["deployment_defaults", "default_control_mode", "triggers_automations_domains", "domain_overrides", "autonomous", "confirm", "read_only", "prohibited"], "issues": ["詞彙表", "canonical_tags", "canonical_roots", "觸發問題", "孤立", "重新整理"], "clear-orphans": ["孤立策略", "清理"], "portability": ["備份", "存檔", "mesa_export", "format_version"], "import": ["on_conflict", "overwrite", "skip", "skipped_existing", "invalid"], "suggestions": ["忽略", "dismissed_suggestions", "恢復建議"] } }
---
這些端點讀取和變更 MESA：各層級的策略、部署預設設定、標籤詞彙表、問題和建議，以及可攜式存檔。登入、錯誤本文和請求體如[管理 API](guide:topic.adminApi)所述。

相關：[錯誤](guide:topic.adminApi#errors)、[請求體](guide:topic.adminApi#bodies)、[MESA 如何決定](guide:topic.mesaRules)

## 適用於每個 MESA 端點 {#shared}

除 `device-options`、`integration-options` 和 `vocabulary` 外，每個 MESA 端點都需要 MESA 正在執行。未執行時，它們傳回：

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 503 | `service_unavailable` | `adminError.mesaUnavailable` | MESA 未執行。 |
| 503 | `service_unavailable` | `adminError.mesaSaveFailed` | Phoenix 無法儲存變更，因此已復原它。 |

每次變更都會新增一筆包含請求路徑的稽核日誌條目。匯出也會新增一筆，儘管它不變更任何內容。

MESA 租約沒有管理端點。Phoenix 把它們儲存在記憶體中，智慧體用自己的工具請求和釋放它們。

## 實體策略 {#entity-profiles}

```
GET    /api/phoenix-mcp/admin/mesa/profiles
GET    /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
PUT    /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
DELETE /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
```

使用它們尋找、讀取、儲存和刪除一個實體的 MESA 策略。

相關：[策略](guide:window.mesaProfiles)、[層級](guide:topic.mesaRules#levels)

### 列出實體策略 {#list-profiles}

列出符合每個篩選條件的已儲存實體策略，一次一頁。推斷和未知來源也會包含。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `domain` | query, string | 僅列出此域中的實體。 |
| `tag` | query, string | 僅列出有效標籤包含此標籤的策略。 |
| `area` | query, string | 僅列出此分區中的實體。 |
| `origin` | query, string | `developer`、`user`、`hybrid`、`inferred_ai` 或 `unknown`。 |
| `limit` | query, integer | 預設 50。1 到 200 之外的值按最近的邊界計算。 |
| `cursor` | query, string | 上一頁的 `next_cursor`。第一頁省略它。 |

回應為 `{ profiles, total_matched, has_more, next_cursor }`。`profiles` 中的每一項為 `{ entity_id, document }`。當 `has_more` 為 `true` 時，用 `cursor` 請求下一頁。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.integerRequired` | `limit` 不是整數。`message_params.field` 為 `limit`。 |
| 400 | `invalid_request` | 無，`message_passthrough` | MESA 拒絕了游標或篩選條件。訊息來自 MESA 本身。 |

### 讀取實體策略 {#read-profile}

回應為 `{ entity_id, stored, effective, explanation }`：

- `stored` 是實體自己的策略文件，沒有時為 `null`。
- `effective` 是合併所有層級後 MESA 套用的策略。
- `explanation` 是 MESA 對其如何得出該結果的說明。

相關：[策略如何組合](guide:topic.mesaRules#combining)

### 儲存實體策略 {#save-profile}

儲存實體的策略，替換它原有的策略。傳送一個策略文件：

```json
{
  "semantic_profile": { },
  "privacy_classification": { }
}
```

你也可以傳送一個裸語意策略。讀取回應中的 `stored` 欄位具有相同形狀。Phoenix 會用目前結構描述版本給文件打上標記，如果文件未指定來源，則以來源 `user` 儲存。

若要在不覆蓋現有策略的情況下建立策略，請傳送標頭 `If-None-Match: *`。如果實體已有策略，則不儲存任何內容，你會收到 `412`。

路徑中的實體 ID 必須是一個域、一個點，然後是小寫字母、數字和底線。讀取和刪除使用相同形式。

回應為 `{ entity_id, stored, warnings }`。`warnings` 列出 MESA 為此實體發現的觸發問題，形狀見[問題和標籤詞彙表](guide:#issues)。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | 實體 ID 格式錯誤。 |
| 412 | `precondition_failed` | `adminError.mesaProfileExists` | 你傳送了 `If-None-Match: *`，但策略已存在。 |
| 400 | `invalid_request` | 無，`message_passthrough` | MESA 拒絕了文件。訊息來自 MESA 本身。 |

### 刪除實體策略 {#delete-profile}

移除實體自己的策略，使它再次遵循上層。回應為 `{ entity_id, "deleted": true }`，即使之前沒有策略也是如此。

## 其他層級的策略 {#scope-profiles}

```
GET    /api/phoenix-mcp/admin/mesa/devices
GET    /api/phoenix-mcp/admin/mesa/devices/{device_id}
PUT    /api/phoenix-mcp/admin/mesa/devices/{device_id}
DELETE /api/phoenix-mcp/admin/mesa/devices/{device_id}
GET    /api/phoenix-mcp/admin/mesa/device-options
GET    /api/phoenix-mcp/admin/mesa/areas
GET    /api/phoenix-mcp/admin/mesa/areas/{area_id}
PUT    /api/phoenix-mcp/admin/mesa/areas/{area_id}
DELETE /api/phoenix-mcp/admin/mesa/areas/{area_id}
GET    /api/phoenix-mcp/admin/mesa/integrations
GET    /api/phoenix-mcp/admin/mesa/integrations/{integration}
PUT    /api/phoenix-mcp/admin/mesa/integrations/{integration}
DELETE /api/phoenix-mcp/admin/mesa/integrations/{integration}
GET    /api/phoenix-mcp/admin/mesa/integration-options
GET    /api/phoenix-mcp/admin/mesa/domains
GET    /api/phoenix-mcp/admin/mesa/domains/{domain}
PUT    /api/phoenix-mcp/admin/mesa/domains/{domain}
DELETE /api/phoenix-mcp/admin/mesa/domains/{domain}
```

用於涵蓋裝置、分區、整合或域的策略。它們的運作方式與實體策略相同，只是鍵不同。

| 層級 | 回應中的鍵 | 儲存時的鍵格式 | 錯誤鍵 |
|---|---|---|---|
| 裝置 | `device_id` | 最多 255 個字元，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;` | `adminError.nodeIdTooLong`、`adminError.nodeIdInvalidChars` |
| 分區 | `area_id` | 最多 255 個字元，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;` | `adminError.nodeIdTooLong`、`adminError.nodeIdInvalidChars` |
| 整合 | `integration` | 一個小寫字母，然後是小寫字母、數字和底線 | `adminError.integrationNameInvalid` |
| 域 | `domain` | 一個小寫字母，然後是小寫字母、數字和底線 | `adminError.badDomain` |

相關：[路徑中的 ID](guide:topic.adminApi#ids)

### 清單 {#scope-lists}

每個清單傳回一個以其層級命名的陣列，依鍵排序：`{ devices }`、`{ areas }`、`{ integrations }` 或 `{ domains }`。每一項為 `{ <key>, document }`，例如 `{ device_id, document }`。這些清單不分頁。

### 讀取、儲存和刪除 {#scope-profile}

- 讀取傳回 `{ <key>, stored }`，沒有策略時 `stored` 為 `null`。只有實體讀取會新增 `effective` 和 `explanation`。
- 儲存接受與[儲存實體策略](guide:#save-profile)相同的文件和 `If-None-Match: *` 標頭。它傳回 `{ <key>, stored }`，不含 `warnings`。
- 刪除傳回 `{ <key>, "deleted": true }`，即使之前沒有策略也是如此。
- 只有儲存會檢查鍵格式。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | 上表中該鍵的錯誤 | 鍵格式錯誤。僅限儲存。 |
| 412 | `precondition_failed` | `adminError.mesaProfileExists` | 你傳送了 `If-None-Match: *`，但策略已存在。 |
| 400 | `invalid_request` | 無，`message_passthrough` | MESA 拒絕了文件。訊息來自 MESA 本身。 |

### 選擇器清單 {#picker-lists}

這些清單列出你可以把策略授予什麼，供你自己的應用程式中的選擇器使用。它們在 MESA 關閉時也能運作。

- `device-options` 傳回 `{ devices: [{ id, name }] }`，依名稱排序。名稱是使用者給裝置的名稱，否則是裝置名稱，否則是其 ID。
- `integration-options` 傳回 `{ integrations: [{ id, name }] }`：每個建立過實體的整合，Phoenix MCP 本身除外。

## 部署預設設定 {#defaults}

```
GET    /api/phoenix-mcp/admin/mesa/defaults
PUT    /api/phoenix-mcp/admin/mesa/defaults
```

用於讀取和設定 MESA 對任何層級都沒有策略的實體套用什麼。部署預設設定是三個設定，而不是策略文件。

相關：[內建基線](guide:topic.mesaRules#baseline)

### 讀取預設設定 {#read-defaults}

MESA 自己的格式會再次包裝設定，因此回應會巢狀兩次 `deployment_defaults`。未儲存預設設定時，外層為 `null`：

```json
{
  "deployment_defaults": {
    "deployment_defaults": {
      "default_control_mode": "confirm",
      "triggers_automations_domains": [],
      "domain_overrides": {}
    }
  }
}
```

### 設定預設設定 {#set-defaults}

傳送 `{ "deployment_defaults": { ... } }`，或只傳送內部物件，且只包含這些鍵：

| 欄位 | 類型 | 說明 |
|---|---|---|
| `default_control_mode` | string | `autonomous`、`confirm`、`read_only` 或 `prohibited`。預設 `confirm`。 |
| `triggers_automations_domains` | 字串陣列 | 域，以字串表示。 |
| `domain_overrides` | object | 以域為鍵。每個值是一個物件。 |

回應的形狀與讀取相同。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.deploymentDefaultsInvalid` | MESA 拒絕了這些預設值。`message_params.detail` 說明原因。 |

## 問題和標籤詞彙表 {#issues}

```
GET    /api/phoenix-mcp/admin/mesa/vocabulary
GET    /api/phoenix-mcp/admin/mesa/issues
POST   /api/phoenix-mcp/admin/mesa/orphans/clear
```

用於讀取 MESA 知道的標籤、尋找與自動化不一致的策略，以及清理已移除項目留下的策略。

### 詞彙表 {#vocabulary}

傳回 `{ canonical_tags, canonical_roots }`，兩者都已排序。它在 MESA 關閉時也能運作。

### 問題、孤立項和建議 {#read-issues}

| 欄位 | 類型 | 說明 |
|---|---|---|
| `refresh` | query, string | 省略它以使用已儲存的清單。`suggestions` 僅重新計算建議。其他任何值會重新計算所有內容。 |

回應為 `{ issues, orphans, orphan_devices, orphan_areas, orphan_integrations, suggestions, dismissed_suggestions }`。

- 一個問題為 `{ entity_id, declared_value, automation_id, role, severity, recommendation }`。
- 建議具有相同形狀，另加一個用於忽略或恢復它們的 `key`。
- 孤立項清單列出其實體、裝置、分區或整合已不存在的策略。

### 清除孤立項 {#clear-orphans}

刪除每個孤立的實體、裝置、分區和整合策略。不傳送本文。Phoenix 會先對照 Home Assistant 檢查孤立項，然後準確刪除那些策略。

回應為 `{ deleted: { entities, devices, areas, integrations }, count }`。

## 匯入和匯出 {#portability}

```
GET    /api/phoenix-mcp/admin/mesa/export
POST   /api/phoenix-mcp/admin/mesa/import
```

用於把所有 MESA 策略和部署預設設定複製到檔案，再複製回來。

### 匯出 {#export}

把所有策略傳回為一個存檔：

```json
{
  "mesa_export": {
    "format_version": "1.1",
    "exported_at": "...",
    "mesa_core_version": "...",
    "entities": {},
    "domains": {},
    "integrations": {},
    "areas": {},
    "devices": {},
    "deployment_defaults": {},
    "unreadable_profiles": []
  }
}
```

每個部分把策略的鍵映射到其文件。`deployment_defaults` 僅在儲存了預設設定時出現。MESA 無法讀取的已儲存策略會被排除，其鍵列在 `unreadable_profiles` 中，後者僅在此時出現。

### 匯入 {#import}

檢查存檔中的每條策略並儲存有效的策略。Phoenix 絕不儲存無效策略；它會報告該策略。之後它會重新計算孤立項和建議。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `archive` | object | 必填。一個匯出檔，根處為 `mesa_export`，`format_version` 為 `1.0` 或 `1.1`。 |
| `on_conflict` | string | `skip`（預設）保留你已有的策略。`overwrite` 替換它們。 |

回應為 `{ imported, overwritten, skipped_existing, invalid }`：

- `imported` 和 `overwritten` 是計數。
- `skipped_existing` 列出因已存在而保留的策略。
- `invalid` 把每個被拒絕的策略映射到原因。
- 策略命名為 `<section>:<key>`，例如 `entities:light.kitchen`，或 `deployment_defaults`。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.mesaArchiveRequired` | 缺少 `archive`。 |
| 400 | `invalid_request` | `adminError.mesaConflictModeInvalid` | `on_conflict` 不是 `skip` 或 `overwrite`。 |
| 400 | `invalid_request` | 無，`message_passthrough` | 存檔不是 MESA 匯出檔，或其 `format_version` 不受支援。 |

## 建議 {#suggestions}

```
POST   /api/phoenix-mcp/admin/mesa/suggestions/dismiss
POST   /api/phoenix-mcp/admin/mesa/suggestions/restore
```

用於隱藏你不想要的策略建議，或恢復已忽略的建議。建議本身請從[問題、孤立項和建議](guide:#read-issues)讀取。

相關：[建議](guide:window.mesaSuggestions)

### 忽略建議 {#dismiss-suggestion}

| 欄位 | 類型 | 說明 |
|---|---|---|
| `key` | string | 必填，不能為空。目前清單中某個建議的 `key`。 |

回應為 `{ dismissed, suggestions, dismissed_suggestions }`，其中 `dismissed` 是該鍵。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.suggestionKeyRequired` | `key` 缺少或為空。 |
| 404 | `not_found` | `adminError.suggestionNotCurrent` | 目前沒有建議具有此鍵。 |

### 恢復建議 {#restore-suggestion}

為一個已忽略建議傳送 `key`，或為全部傳送 `all: true`。

| 欄位 | 類型 | 說明 |
|---|---|---|
| `key` | string | 一個已忽略建議的鍵。 |
| `all` | boolean | `true` 恢復每個已忽略建議。 |

回應為 `{ restored, suggestions, dismissed_suggestions }`。`restored` 是該鍵，或者使用 `all` 時是被清除的忽略數量。

| 狀態 | `error` | `message_key` | 條件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.suggestionRestoreTargetRequired` | `key` 和 `all: true` 都沒有傳送。 |
| 404 | `not_found` | `adminError.suggestionNotDismissed` | 該鍵未被忽略。 |
