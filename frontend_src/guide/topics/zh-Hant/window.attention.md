---
{ "id": "window.attention", "title": "{{label:attention.title}}", "parent": "topic.windows", "order": 22, "keywords": ["未知結果", "已中斷", "被阻止的寫入", "審核"], "sectionKeywords": { "actions": ["檢查先前的操作", "未知", "行事曆"], "review": ["標記為已核對", "在稽核日誌中顯示", "被阻止", "寫入被拒絕"], "problems": ["MESA 無法啟動", "行事曆歷史"], "window": ["顯示需要關注", "關閉"] } }
---
Phoenix 自己無法解決的事情。有內容需要檢查時，視窗會出現在工作區上方；清單為空時它會消失。它每 15 秒檢查一次新項目。

## 結果未知的操作 {#actions}

操作已到達 Home Assistant，但 Phoenix 始終不知道它是否生效。這會在對話被切斷或 Home Assistant 在操作中途重新啟動時發生。項目會註明存取金鑰、工具以及開始時間。

在你審核它之前，該存取金鑰無法再進行任何變更。結果未知的行事曆更改也會以相同方式列出。

## 審核操作 {#review}

在 Home Assistant 中檢查操作是否發生。對於智慧體的操作，**{{label:attention.showInAudit}}** 會在該請求處開啟稽核日誌。

然後選擇 **{{label:attention.continue}}**，勾選 **{{label:attention.checked}}** 並確認。Phoenix 不會重複或復原該操作。金鑰可以再次進行變更，稽核日誌會記錄你的審核。

## 需要修復的問題 {#problems}

- **{{label:attention.mesaTitle}}**：MESA 啟動失敗，因此受保護的變更被暫停。請檢查 Home Assistant 日誌，然後重新載入 Phoenix。
- **{{label:attention.calendar_storageTitle}}**：行事曆變更會暫停，直到 Phoenix 能再次讀取其儲存的行事曆歷史。

問題修復後，這些會自行消失。

## 關閉視窗 {#window}

關閉視窗會在目前工作區隱藏它，直到出現它未顯示過的項目。工作區選項中的 **{{label:workspace.showAttention}}** 會把它帶回來，重設目前工作區或所有工作區也會如此。在手機上，請從標題列關閉它。
