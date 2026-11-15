---
{ "id": "window.settingsProviders", "title": "{{label:workspace.aiProviders}}", "parent": "workspace.settings", "order": 1, "keywords": ["API 金鑰", "模型", "LLM", "帳戶", "額度", "TypeSafe", "JEV", "Clef", "Gemini Decision"], "sectionKeywords": { "provider-add": ["新帳戶", "Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenAI-Compatible", "llama.cpp", "Bionic", "LM Studio", "vLLM", "LocalAI", "OpenRouter", "Infron", "Together AI", "Cerebras", "Cloudflare Workers AI", "Workers AI", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM", "深度求索", "通義千問", "千問", "阿里雲百煉", "百煉", "智譜", "月之暗面", "騰訊雲", "小米", "離線", "本機模型", "價格", "計費"], "decision-threshold": ["threshold", "confidence", "probability", "0.90"], "decision-provider": ["TypeSafe", "JEV", "快速路徑", "OpenCode", "System One", "Gemini Decision", "GPT Luna Decision", "Cloudflare", "Workers AI", "Clef", "Clef Flash", "帳戶 ID", "谷歌", "OpenAI Decisions", "gpt-6-luna"], "tool-loading": ["load_tools", "延遲載入", "工具搜尋", "token", "工具太多", "上下文"] } }
---
新增 Phoenix 使用的 AI 帳戶。每個帳戶需要 API 金鑰，或者對於本機模型，需要伺服器位址。

Agent Chat、語音智慧體和 AI 任務各使用其中一個帳戶，並搭配你選擇的模型。它們會把對話和工具結果傳送給該提供者。

相關：[哪些資料會離開你的家](guide:topic.security#leaves-home)

## 帳戶 {#provider-accounts}

列出每個帳戶及其提供者和預設模型。選擇一個帳戶以顯示其選項，再次選擇則隱藏它們。

如果帳戶有問題，例如提供者不再提供某個模型，其選項中會顯示警告。

### {{label:settings.accountChangeModel}} {#change-model}

從提供者提供的模型中選擇。無法使用工具的模型會顯示為灰色，因為 Phoenix 需要工具來執行操作。

**{{label:settings.agentcliModelAuto}}** 使用適合代理工作的目前模型，略過預覽版和已宣布停用的模型。如果你自己選擇模型，Phoenix 會一直保留它，直到提供者停用它。

之後會切換到提供者指定的後續模型。沒有的話，切換到同系列中最新的模型，再沒有就切換到同一檔次中最新的模型，例如另一個 Haiku 模型。

如果都沒有，Phoenix 會保留已停用的模型並顯示警告，請另選一個模型。名稱以 `:free` 結尾的模型只會切換到另一個免費模型。在本機 Ollama 伺服器上，Phoenix 只在同一系列內切換，**{{label:settings.agentcliModelAuto}}** 也從不選擇雲端模型。

當 **{{label:settings.agentcliVerifyOptions}}** 開啟時，儲存時還會檢查模型支援哪些選項。這會消耗少量 API 額度。

### {{label:settings.accountRefreshModels}} {#refresh-models}

重新載入提供者的模型清單。這不消耗 token。在本機 Ollama 伺服器上，Phoenix 會逐個詢問每個模型。

### {{label:settings.accountCheckOptions}} {#check-options}

透過傳送幾個非常簡短的請求，查明模型支援哪些努力層級以及是否能讀取影像。

這會消耗少量額度，因此 Phoenix 會先詢問。將指標懸停在按鈕上可查看上次執行的時間。

### 移除帳戶 {#remove-account}

移除帳戶會刪除其儲存的金鑰。你需要先就地確認。

如果語音智慧體或 AI 任務使用了該帳戶，它們會失去它，Phoenix 會移除為它們建立的 Assist 助理和 AI 任務實體。

相關：[語音智慧體](guide:window.settingsHome#voice-agent)、[AI 任務](guide:window.settingsHome#ai-task)

### {{label:settings.toolLoadingLabel}} {#tool-loading}

決定 Agent Chat、語音智慧體和 AI 任務如何把存取金鑰的工具交給此帳戶的模型。

**{{label:settings.toolLoadingAuto}}**（預設）只傳送一小組常用工具，其餘工具依群組列出。請求需要時，模型會載入更多工具。存取金鑰的工具很多（約 75 個或以上）時，Phoenix 才這樣做；工具較少時，會全部傳送。

分批載入讓每個請求傳送的 token 少得多，結果相同。請求需要尚未載入的工具時，會多一步。語音智慧體一開始就帶有裝置控制工具，因此語音命令不用等這一步。

**{{label:settings.toolLoadingAlways}}** 無論工具多少都分批載入。**{{label:settings.toolLoadingNever}}** 一律傳送全部工具，但只接受 128 個工具的提供者除外。

工具載入不會給模型更多權限，Phoenix 照常檢查每次呼叫。Home Companion 金鑰不受 **{{label:settings.toolLoadingLabel}}** 設定影響。

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
- Cloudflare Workers AI：你的 Cloudflare 帳戶 ID，以及具有 Workers AI 權限的 API 權杖。Phoenix 只列出標記為可使用工具的模型。免費方案每天只有少量額度，部分模型需要 Workers Paid。
- Ollama（本機）：你的 Ollama 伺服器位址。Home Assistant 會連線它，因此請使用 Home Assistant 主機可以存取的位址。一般 `http://` 流量不加密；請參閱[哪些資料會離開你的家](guide:topic.security#leaves-home)。
- {{label:settings.providerLocalOpenAI}}：你的 llama.cpp、vLLM、Bionic 或 LocalAI 伺服器位址，例如 `http://host:8080/v1`。位址沒有路徑時，Phoenix 會加上 `/v1`。只有伺服器需要時才填寫 API 金鑰。

有些方案只供程式設計工具使用：Z.ai 的 Coding Plan、Xiaomi MiMo 的 Token Plan，以及阿里雲的 Coding Plan 和 Token Plan。它們的條款禁止在 Home Assistant 這類應用程式中使用，提供商可能會限流或封鎖金鑰。

選擇這些方案時 Phoenix 會發出警告。語音、AI Task 和 Agent Chat 請使用提供商的按量付費 API。

Groq、Mistral AI 和 Z.ai 每個請求最多接受 128 個工具。Phoenix 對 Gemini、Kimi、OpenRouter、Infron、Cloudflare Workers AI 和本機 OpenAI 相容伺服器也採用同樣的上限。存取金鑰的工具更多時，無論[工具載入](guide:#tool-loading)如何設定，Phoenix 都會為它們分批載入工具。

Groq 的免費方案限制每個模型每分鐘接受的 Token 數，而單一 Phoenix 請求就超過了這個上限。Groq 會拒絕該請求，等待也無濟於事。請使用 Groq 的付費方案或其他供應商。

## {{label:settings.catalogChecks}} {#catalog-checks}

Phoenix 會不時重新讀取每個帳戶的模型清單。這能讓清單保持最新，使 **{{label:settings.agentcliModelAuto}}** 可以切換到更新的模型。

### {{label:settings.providerChecksLabel}} {#provider-checks}

每小時一次，Phoenix 會重新讀取早於以下期間的模型清單。這不消耗 token。預設為開啟。

關閉時，Phoenix 仍會在需要時讀取清單：新帳戶、提供者回報缺少模型，以及你切換到 **{{label:settings.agentcliModelAuto}}** 時。

### {{label:settings.providerChecksHoursLabel}} {#provider-checks-hours}

Phoenix 會按排程重新檢查提供者的模型清單：6 到 720 小時（預設 24）。

## {{label:routing.providerSection}} {#decision-provider}

決策提供者是一個小型快速模型，負責執行簡單命令，這樣你的主 AI 模型就不必處理。它節省完整模型輪次的費用和等待時間。

它與 **{{label:routing.enableChat}}** 和 **{{label:routing.enableVoice}}** 搭配使用。Phoenix 會先嘗試 Home Assistant 自己的句子比對，決策提供者處理句子比對遺漏的命令。

從以下決策提供者中選擇：

- TypeSafe / JEV：TypeSafe 的 JEV 模型，使用 TypeSafe API 金鑰。
- OpenCode / System One：JEV、Gemini Decision 或 GPT Luna Decision，使用 OpenCode Console API 金鑰。
- Google / Gemini Decision：Google 的 Gemini Decision 模型，使用 Gemini API 金鑰。
- Cloudflare / Clef：Workers AI 上的 Clef 或 Clef Flash，使用你的 Cloudflare 帳戶 ID 和 API 權杖。
- OpenAI / Decisions：OpenAI 的 gpt-6-luna 決策模型，使用 OpenAI API 金鑰。

新增一個帳戶並選擇其模型。同一時間只能使用一個決策提供者。每個帳戶的名稱旁會顯示其模型。

決策帳戶與同一家公司的任何 AI 提供者帳戶彼此獨立。如果你已經在 AI 提供者中使用該金鑰，也必須在此再次新增。

除 Cloudflare / Clef 外，所有決策提供者也會處理依賴對話內容的後續訊息。Clef 會把這類訊息交給你的 AI 提供者。參見[後續訊息](guide:topic.commandRouting#follow-ups)。

jev-preview 模型只觀察，從不執行命令。

若要使用它，請選擇其帳戶。選擇 **{{label:routing.noProvider}}** 可停止使用它；命令隨後只依賴句子比對。

它也可以為自動化和腳本回答 Home Assistant 的「評估」動作，請參閱[決策](guide:window.settingsHome#ai-decisions)。

相關：[命令如何路由](guide:topic.commandRouting)

### {{label:routing.decisionThreshold}} {#decision-threshold}

決策帳戶執行命令前必須達到的確定程度，範圍 0.70 到 1.00（預設 0.90）。選擇帳戶即可顯示此設定。每個帳戶各自保存。

閾值越低，決策提供者自己處理的命令越多，轉給你的 AI 提供者的命令越少。這可能會讓更多錯誤通過，因為每個模型各不相同。

## {{label:routing.connectionSettings}} {#decision-connection}

這些限制在使用決策提供者時適用。

### {{label:routing.decisionBudget}} {#decision-timeout}

Phoenix 等待決策提供者的時間，從 250 到 30,000 毫秒（預設 5,000）。如果提示逾時，請求會照常交給你的主 AI 模型。

### {{label:routing.requestLimit}} {#decision-request-limit}

Phoenix 每小時可以傳送的最大決策請求數。設為 0（預設）表示不限制。每次使用決策提供者都會增加計數，包括失敗的請求。

達到限制後，命令會交給你的主 AI 模型，直到該小時結束。
