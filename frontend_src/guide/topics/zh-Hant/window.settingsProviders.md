---
{ "id": "window.settingsProviders", "title": "{{label:workspace.aiProviders}}", "parent": "workspace.settings", "order": 1, "keywords": ["API 金鑰", "模型", "LLM", "帳戶", "額度", "TypeSafe", "JEV"], "sectionKeywords": { "provider-add": ["新帳戶", "OpenAI", "Anthropic", "Claude", "Gemini", "Ollama"], "decision-provider": ["TypeSafe", "JEV", "快速路徑"] } }
---
新增 Phoenix 使用的 AI 帳戶。每個帳戶需要 API 金鑰，或者對於本機模型，需要伺服器位址。

Agent Chat、語音智慧體和 AI 任務各使用其中一個帳戶，並搭配你選擇的模型。

## 帳戶 {#provider-accounts}

列出每個帳戶及其提供者和預設模型。選擇一個帳戶以顯示其選項，再次選擇則隱藏它們。

如果帳戶有問題，例如提供者不再提供某個模型，其選項中會顯示警告。

### {{label:settings.accountChangeModel}} {#change-model}

從提供者提供的模型中選擇。無法使用工具的模型會顯示為灰色，因為 Phoenix 需要工具來執行操作。

**{{label:settings.agentcliModelAuto}}** 一律使用提供者最新且合適的模型。如果你自己選擇模型，Phoenix 會一直保留它，直到提供者停用它，然後切換到其後續模型。

當 **{{label:settings.agentcliVerifyOptions}}** 開啟時，儲存時還會檢查模型支援哪些選項。這會消耗少量 API 額度。

### {{label:settings.accountRefreshModels}} {#refresh-models}

重新載入提供者的模型清單。這不消耗 token。在本機 Ollama 伺服器上，Phoenix 會逐個詢問每個模型。

### {{label:settings.accountCheckOptions}} {#check-options}

透過傳送幾個單一 token 請求，查明模型支援哪些努力層級以及是否能讀取影像。

這會消耗少量額度，因此 Phoenix 會先詢問。將指標懸停在按鈕上可查看上次執行的時間。

### 移除帳戶 {#remove-account}

移除帳戶會刪除其儲存的金鑰。你需要先就地確認。

如果語音智慧體或 AI 任務使用了該帳戶，它們會失去它，Phoenix 會移除為它們建立的 Assist 助理和 AI 任務實體。

相關：[語音智慧體](guide:window.settingsHome#voice-agent)、[AI 任務](guide:window.settingsHome#ai-task)

## 新增帳戶 {#provider-add}

1. 選擇工具列上的 **{{label:settings.addAccount}}** 以顯示提供者。
2. 選擇一個提供者並填寫其欄位。選擇 **{{label:settings.showSecret}}** 可檢查你貼上的 API 金鑰。
3. 選擇 **{{label:settings.agentcliValidate}}**。這會向提供者核實詳細資料，但暫時不儲存任何內容。
4. 選擇一個模型，或保留 **{{label:settings.agentcliModelAuto}}**，然後選擇 **{{label:settings.agentcliDone}}**。

如果驗證後變更了欄位，請再次驗證。Phoenix 會在儲存帳戶前再檢查一次詳細資料。

### 每個提供者要求什麼 {#provider-kinds}

大多數提供者只需要 API 金鑰。有些還需要知道你的帳戶所在位置：

- OpenAI、Grok、Kimi、Mistral AI 和 MiniMax：金鑰簽發所在的區域。
- Xiaomi MiMo：按用量付費，或你的 Token Plan 區域。
- Tencent Cloud TokenHub：簽發金鑰的站點。金鑰只能在該站點使用。
- OpenCode：Zen 或 Console 路徑。你只能在其中一條路徑上新增金鑰。
- Z.ai：Standard API、Coding Plan 或 China。同一個金鑰可以為每個方案各儲存一次。
- Qwen：除了 API 金鑰，還需要你的工作區基礎 URL，即一個公開的 HTTPS 位址。
- Ollama（本機）：你的 Ollama 伺服器位址。Home Assistant 會連線它，因此請使用 Home Assistant 主機可以存取的位址。

OpenAI、Gemini、Groq、Kimi、Mistral AI、OpenRouter 和 Z.ai 每個請求最多接受 128 個工具。Phoenix 會先給它們較小的工具集，並在模型要求時載入更多。

## {{label:settings.catalogChecks}} {#catalog-checks}

Phoenix 會不時重新讀取每個帳戶的模型清單。這能讓清單保持最新，使 **{{label:settings.agentcliModelAuto}}** 可以切換到更新的模型。

### {{label:settings.providerChecksLabel}} {#provider-checks}

每小時一次，Phoenix 會重新讀取早於以下期間的模型清單。這不消耗 token。預設為開啟。

關閉時，Phoenix 仍會在需要時讀取清單：新帳戶、提供者回報缺少模型，以及你切換到 **{{label:settings.agentcliModelAuto}}** 時。

### {{label:settings.providerChecksHoursLabel}} {#provider-checks-hours}

模型清單在 Phoenix 重新讀取前可以有多舊：6 到 720 小時（預設 24）。檢查關閉時顯示為灰色。

## {{label:routing.providerSection}} {#decision-provider}

決策提供者是一個小型快速模型，負責執行簡單命令，這樣你的主 AI 模型就不必處理。它節省完整模型輪次的費用和等待時間。

它與 **{{label:routing.enableChat}}** 和 **{{label:routing.enableVoice}}** 搭配使用。Phoenix 會先嘗試 Home Assistant 自己的句子比對，決策提供者處理句子比對遺漏的命令。

Phoenix 提供一個決策提供者：TypeSafe / JEV。在此新增你的帳戶並選擇模型。

若要使用它，請選擇其帳戶。選擇 **{{label:routing.noProvider}}** 可停止使用它；命令隨後只依賴句子比對。

相關：[命令如何路由](guide:topic.commandRouting)

## {{label:routing.connectionSettings}} {#decision-connection}

這些限制在使用決策提供者時適用。

### {{label:routing.decisionBudget}} {#decision-timeout}

Phoenix 等待決策提供者的時間，從 250 到 30,000 毫秒（預設 5,000）。如果提示逾時，請求會照常交給你的主 AI 模型。

### {{label:routing.requestLimit}} {#decision-request-limit}

Phoenix 每小時可以傳送的最大決策請求數。設為 0（預設）表示不限制。每次使用決策提供者都會增加計數，包括失敗的請求。

達到限制後，命令會交給你的主 AI 模型，直到該小時結束。
