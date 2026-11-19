---
{ "id": "window.logDetails", "title": "{{label:workspace.logDetails}}", "parent": "workspace.logs", "order": 3, "keywords": ["條目", "記錄", "負載", "逐字稿"], "sectionKeywords": { "step": ["上一個", "下一個"], "audit-entry": ["負載", "請求 ID", "參數"], "conversation": ["輪次", "複製", "原始資料", "技術詳細資料", "工具結果"] } }
---
你在[{{label:shell.tabAudit}}](guide:window.auditLog)或[{{label:conversations.title}}](guide:window.conversationLog)中選擇的記錄，完整顯示。

## 在記錄間切換 {#step}

**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 會依日誌目前顯示的順序移動。它們也會到達之後才出現的條目，並在你重新排序日誌時跟隨新的順序。

## 稽核條目 {#audit-entry}

條目的每個欄位，包括其{{label:audit.rowRequestId}}。工具呼叫還會顯示其{{label:audit.rowPayload}}：工具的名稱和參數，其中密碼、權杖、金鑰以及警報或門鎖代碼會被隱藏。過長的負載會被截斷。

如果存取金鑰事後被重新命名，它的舊名稱會顯示在目前名稱後的括號中。

應用程式透過 OAuth 登入發出的請求會顯示{{label:audit.rowOAuthApp}}：先是應用程式自稱的名稱，然後是它登入時使用的位址。只有位址經過核實，請以位址為準。

由 Phoenix 的 AI 模型發出的請求會顯示其{{label:conversations.providerLabel}}和{{label:conversations.modelLabel}}。這類請求來自 Agent Chat、語音智慧體、AI 任務、Home Companion 和決策提供者。如果請求曾等待核准，記錄核准結果的條目也會顯示同一個模型。

## 對話 {#conversation}

標題會概括對話：來自哪裡、何時，以及包含多少輪次、工具呼叫和變更。其後是各個輪次，最早的在前，每次 25 個。

每個輪次顯示詢問的內容、每次工具呼叫及其結果、帶變更前後內容的變更，以及回覆。長文字會被摺疊。**{{label:conversations.copy}}** 會複製一段文字。**{{label:conversations.details}}** 和 **{{label:conversations.technical}}** 會顯示精確記錄的內容。
