---
{ "id": "topic.logRecords", "title": "日誌記錄的內容", "parent": "workspace.logs", "order": 10, "keywords": ["稽核", "歷史", "保留", "隱私", "遮蔽"], "sectionKeywords": { "audit-entries": ["工具呼叫", "管理", "核准", "MESA"], "not-audited": ["缺少", "401", "未授權"], "outcomes": ["已允許", "已拒絕存取", "速率受限", "待核准", "無效請求", "未找到", "未實現"], "audit-keeping": ["保留", "儲存", "磁碟", "清除"], "conversation-sources": ["agent chat", "語音", "AI 任務", "Assist", "MCP 用戶端", "Home Companion"], "conversation-contents": ["工具結果", "提供者", "模型", "機密", "代碼"], "conversation-keeping": ["保留", "儲存", "限制"], "linked": ["請求 ID", "核准 ID"] } }
---
Phoenix 保留兩種日誌。稽核日誌是每個操作及其結果的簡短記錄。對話記錄在開啟後會完整保留智慧體說過和做過的事情。

## 什麼構成稽核條目 {#audit-entries}

- **智慧體操作**：來自 MCP 用戶端、Agent Chat、語音智慧體、Assist、AI 任務或 Home Companion 的每次工具呼叫。條目包含工具名稱和參數，絕不包含其結果。
- **其他 MCP 請求**：列出工具、開始和結束工作階段，以及金鑰無權發出的請求。如果應用程式在變更期間中斷連線，名為 `request/cancelled` 的條目會記錄這一情況。
- **MESA 阻止**：MESA 每拒絕一個實體就增加一筆條目。這些條目需要 mesa_core.audit 記錄器處於預設層級 INFO；在 Home Assistant 的記錄器設定中提高層級會隱藏它們，但阻止仍會發生。
- **核准**：每個進入核准佇列的請求，以及它的後續結果：已核准、已執行、已拒絕、已取消或失敗。
- **你在面板中的變更**：存取金鑰、權限、設定、MESA 策略、還原、清除等管理員操作，顯示為管理員而非存取金鑰。設定變更會說明改了什麼，即使關閉日誌記錄。

## 什麼不被記錄 {#not-audited}

沒有有效存取金鑰的請求、Phoenix 啟動期間或緊急停止開關阻止的請求，以及不呼叫任何工具的聊天或語音輪次。大多數面板讀取也不被記錄。

開啟[對話記錄](guide:window.settingsLogs#conversation-log)以查看對話本身。

## 結果 {#outcomes}

- **{{label:audit.outcomeAllowed}}**：已執行。
- **{{label:audit.outcomeDenied}}**：被權限或 MESA 拒絕存取，或被拒絕、過期、取消的核准。
- **{{label:audit.outcomePendingApproval}}**：等待你的核准。
- **{{label:audit.outcomeRateLimited}}**：金鑰傳送過多請求。
- **{{label:audit.outcomeInvalidRequest}}**、**{{label:audit.outcomeNotFound}}** 和 **{{label:audit.outcomeNotImplemented}}**：請求格式錯誤、指向不存在的對象，或要求 Phoenix 不支援的操作。Phoenix 無法確認的變更也記為{{label:audit.outcomeInvalidRequest}}，儘管它可能已經生效。

記錄哪些結果由[稽核日誌設定](guide:window.settingsLogs#audit-log)決定。

## 稽核條目保留多久 {#audit-keeping}

稽核日誌預設保留最新的 10,000 筆條目，並每 15 分鐘以及 Home Assistant 停止時儲存到磁碟。可在[{{label:workspace.logsNotifications}}](guide:window.settingsLogs#log-max-entries)中變更這兩項。

面板中沒有任何操作會自行清除稽核日誌。Phoenix 核心資料清除會清除它。移除 Phoenix 會保留它。

## 對話記錄記錄什麼 {#conversation-sources}

- **Agent Chat、語音智慧體和 AI 任務**：每個輪次，包含你的話、回覆以及每次工具呼叫。
- **MCP 用戶端、Assist 和 Home Companion**：每次工具呼叫及其結果。Phoenix 看不到使用者輸入的內容，因此沒有提示。

輪次內完成的工作，例如之後執行的已核准操作，會加入該輪次。同一聊天中的 Agent Chat 輪次會歸為一組。

## 對話包含什麼 {#conversation-contents}

每次工具呼叫及其完整結果、帶變更前後內容的變更、核准、錯誤，以及請求經過的路由。來自 Agent Chat、語音和 AI 任務的條目還會註明 AI 提供者和模型。

密碼、權杖、金鑰以及警報或門鎖代碼在儲存前會被隱藏。影像和檔案只做記錄，不會儲存。Phoenix 給 AI 的指令不會被記錄，token 用量則進入路由統計。

## 對話保留多久 {#conversation-keeping}

預設保留最新的 100 個對話，最長 7 天、25 MB。在[{{label:workspace.logsNotifications}}](guide:window.settingsLogs#conversation-log)中變更限制。過大的條目會保留其結果，但捨棄部分詳細資料。

使用對話記錄視窗中的 **{{label:conversations.clear}}** 清除日誌。核心資料清除也會清除它。

## 在兩種日誌中找到同一個請求 {#linked}

工具呼叫的請求 ID 在兩種日誌中相同，核准的 ID 也相同。在稽核日誌中，依 **{{label:audit.rowRequestId}}** 或 **{{label:audit.rowResource}}** 篩選；在對話記錄中，搜尋它。
