---
{ "id": "window.logDetails", "title": "{{label:workspace.logDetails}}", "parent": "workspace.logs", "order": 3, "keywords": ["條目", "記錄", "負載", "逐字稿"], "sectionKeywords": { "step": ["上一個", "下一個"], "audit-entry": ["負載", "請求 ID", "參數"], "conversation": ["輪次", "複製", "原始資料", "技術詳細資料", "工具結果"] } }
---
你在[{{label:shell.tabAudit}}](guide:window.auditLog)或[{{label:conversations.title}}](guide:window.conversationLog)中選擇的記錄，完整顯示。

## 在記錄間切換 {#step}

**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 會依你選擇記錄時日誌顯示的順序移動。之後到達的條目不會包含在內，直到你再次選擇記錄。

## 稽核條目 {#audit-entry}

條目的每個欄位，包括其 **{{label:audit.rowRequestId}}**。工具呼叫還會顯示其 **{{label:audit.rowPayload}}**：工具的名稱和參數，其中密碼、權杖、金鑰以及警報或門鎖代碼會被隱藏。過長的負載會被截斷。

如果存取金鑰事後被重新命名，它的舊名稱會顯示在目前名稱後的括號中。

## 對話 {#conversation}

標題會概括對話：來自哪裡、何時，以及包含多少輪次、工具呼叫和變更。其後是各個輪次，最早的在前，每次 25 個。

每個輪次顯示詢問的內容、每次工具呼叫及其結果、帶變更前後內容的變更，以及回覆。長文字會被摺疊。**{{label:conversations.copy}}** 會複製一段文字。**{{label:conversations.details}}** 和 **{{label:conversations.technical}}** 會顯示精確記錄的內容。
