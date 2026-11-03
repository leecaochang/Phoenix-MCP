---
{ "id": "wizard.provider", "title": "設定模型提供者", "parent": "workspace.accessKeys", "order": 104 }
---
Agent Chat 會使用你自己的帳戶，把對話傳送給你選擇的模型提供者。

選擇一個已新增的帳戶，或新增一個：選擇提供者、填寫其欄位，然後選擇 **{{label:settings.agentcliValidate}}**。大多數提供者只需要 API 金鑰；有些還會要求區域，本機 Ollama 伺服器則需要其位址。

驗證後，選擇一個模型，或保留 **{{label:settings.agentcliModelAuto}}**。檢查模型設定的選項預設為開啟，會消耗少量 API 額度。

**{{label:wizard.tryNow}}** 會儲存新帳戶，然後用新金鑰開啟 Agent Chat，並已輸入一個測試問題。在此之前不會儲存任何內容。
