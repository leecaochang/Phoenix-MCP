---
{ "id": "window.settingsLogs", "title": "{{label:workspace.logsNotifications}}", "parent": "workspace.settings", "order": 5, "keywords": ["日誌記錄", "記錄"], "sectionKeywords": { "audit-log": ["稽核"], "log-max-entries": ["保留"], "conversation-log": ["歷史", "保留"], "privacy": ["遮蔽"], "log-client-ip": ["IP 位址"], "notifications": ["通知", "警示"] } }
---
選擇 Phoenix 記錄什麼、保留多久，以及 Home Assistant 何時通知你。此視窗也可以從{{label:workspace.logs}}工作區中的 **{{label:logs.logSettings}}** 開啟。

## {{label:shell.tabAudit}} {#audit-log}

稽核日誌會記錄智慧體透過 Phoenix 做了什麼，以及管理員變更了什麼，並記錄各自的結果。

相關：[日誌記錄的內容](guide:topic.logRecords)

### {{label:settings.logEnableLabel}} {#log-enable}

記錄稽核條目和對話。關閉它會停止所有記錄；其他日誌設定隨後會顯示為灰色。預設為開啟。

Phoenix 的感測器會繼續計數，通知仍會到達。

### {{label:settings.logAllowedLabel}} {#log-allowed}

記錄被執行的操作。預設為開啟。

### {{label:settings.logDeniedLabel}} {#log-denied}

記錄被拒絕存取、格式錯誤、不受支援或指向不存在內容的請求。MESA 阻止也計在這裡。預設為開啟。

等待核准的請求以及你的核准決定一律會被記錄。

### {{label:settings.logRateLimitedLabel}} {#log-rate-limited}

記錄因金鑰傳送過多而被拒絕的請求。預設為開啟。

這三個開關只影響新條目。已有條目保持不變。

### {{label:settings.flushIntervalLabel}} {#log-flush}

稽核日誌儲存到磁碟的頻率：每 5、10、15（預設）、30 或 60 分鐘，或從不。Home Assistant 停止或 Phoenix 重新載入時也會儲存。

### {{label:settings.maxLogEntriesLabel}} {#log-max-entries}

稽核日誌保留多少筆條目：100、1,000、5,000 或 10,000（預設）。降低它會立即移除最舊的條目。磁碟上的副本會在下次儲存時縮小。

## {{label:conversations.title}} {#conversation-log}

**{{label:conversations.enable}}** 會保留智慧體說了什麼、做了什麼：Agent Chat 和語音對話、AI 任務，以及 MCP 用戶端、Assist 和 Home Companion 的工具呼叫。在{{label:workspace.logs}}工作區中檢視。預設為關閉。關閉它會保留已記錄的內容。

三個限制控制保留多少內容。每個限制在你變更後片刻儲存。日誌超過限制時，最舊的對話會先被移除。

### {{label:conversations.limit.entries}} {#conversation-entries}

保留多少個對話：1 到 2,000（預設 100）。

### {{label:conversations.limit.days}} {#conversation-days}

對話保留多少天：1 到 365（預設 7）。

### {{label:conversations.limit.mb}} {#conversation-size}

日誌可以佔用多少空間：1 到 250 MB（預設 25）。目前使用的空間會顯示在限制下方。

## {{label:settings.privacySection}} {#privacy}

選擇日誌顯示關於你家和使用 Phoenix 的人的哪些資訊。

### {{label:settings.logEntityIdsLabel}} {#log-entity-ids}

關閉時，稽核日誌的 {{label:audit.rowResource}} 欄會顯示 [redacted]，對話記錄會省略說了什麼和做了什麼。預設為開啟。

稽核日誌的負載也會隱藏實體 ID，但工具呼叫參數中的自由文字仍可能提到一個。已記錄的對話內容會在 Phoenix 下次啟動時移除。

### {{label:settings.logClientIpLabel}} {#log-client-ip}

關閉時，兩種日誌都會隱藏呼叫方的 IP 位址，但寫入工具呼叫參數中的位址會留在稽核負載中。預設為開啟。Agent Chat、語音、Assist、AI 任務和 Home Companion 仍會依名稱顯示，因為它們不是位址。

## {{label:settings.haNotifications}} {#notifications}

Phoenix 可以傳送 Home Assistant 通知，它們會出現在 Home Assistant 側邊欄中。

### {{label:settings.notifyApprovalLabel}} {#notify-approval}

為每個等待你核准的請求傳送一則帶連結的通知。你做出決定後它會消失。預設為開啟。工具列上的核准計數無論如何都會顯示。

### {{label:settings.notifyRateLimitLabel}} {#notify-rate-limit}

當存取金鑰傳送請求的速度超過其限制時通知你。預設為關閉。每個金鑰有一則通知，每分鐘最多更新一次。
