---
{ "id": "window.conversationLog", "title": "{{label:conversations.title}}", "parent": "workspace.logs", "order": 2, "keywords": ["對話", "聊天歷史", "記錄"], "sectionKeywords": { "recording": ["記錄對話", "開啟", "關閉"], "list": ["提示", "輪次", "工具呼叫", "變更", "載入更多"], "filters": ["搜尋", "來源", "結果", "日期"], "updates": ["暫停", "重新整理"], "clear": ["刪除歷史", "清除"] } }
---
智慧體說過和做過的事情，最新的在前。同一聊天中的 Agent Chat 輪次會顯示為一個對話。

相關：[日誌記錄的內容](guide:topic.logRecords#conversation-sources)

## 開啟記錄 {#recording}

在你於[{{label:workspace.logsNotifications}}](guide:window.settingsLogs#conversation-log)中開啟 **{{label:conversations.enable}}** 之前，日誌是空的。選擇工具列上的 **{{label:logs.logSettings}}** 可在此開啟它。記錄預設為關閉。

## 清單 {#list}

每一列顯示最先詢問的內容、結果、來源，以及它包含多少輪次、工具呼叫和變更。選擇一列，即可在[{{label:workspace.logDetails}}](guide:window.logDetails)中閱讀它。

顯示工具名稱而不是提示的列，是來自 MCP 用戶端、Assist 或 Home Companion 的工具呼叫。它們不會傳遞使用者說的話。

清單顯示 25 個對話；選擇 **{{label:common.loadMore}}** 載入更多。依最新優先之外的任何順序排序，只會對已載入的列排序。

## {{label:logs.filters}} {#filters}

依來源、結果和日期縮小清單範圍。搜尋會尋找任何已記錄文字：說過的話、工具名稱和參數以及結果。對話中任意位置符合，都會顯示整個對話。

隱藏篩選會停止套用它們，直到你再次顯示。

## 即時更新 {#updates}

清單顯示期間每 5 秒更新一次，日誌詳細資料中開啟的對話也是如此。**{{label:logs.pauseUpdates}}** 會讓它保持不動；暫停期間，**{{label:common.refresh}}** 會載入一次最新內容。

## {{label:conversations.clear}} {#clear}

刪除所有已記錄的對話，無論篩選顯示什麼。就地確認後繼續。此操作無法復原，稽核日誌會記錄你執行了此操作。
