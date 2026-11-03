---
{ "id": "topic.about", "title": "關於 Phoenix MCP", "order": 1, "keywords": ["介紹", "概覽", "是什麼", "入門", "MCP"], "sectionKeywords": { "ways": ["agent chat", "外部應用程式", "Claude", "ChatGPT", "用戶端"], "checks": ["能力", "權限", "MESA", "核准", "層"], "token": ["長期存取權杖", "LLAT", "權杖"], "start": ["精靈", "設定智慧體", "第一步"] } }
---
Phoenix MCP 讓 AI 智慧體安全地在你的 Home Assistant 中工作。每個智慧體都有自己的存取金鑰，由你決定該金鑰可以檢視和執行什麼。

## 兩種使用方式 {#ways}

- **Agent Chat**：直接在 Home Assistant 內與 AI 模型聊天。你新增 AI 提供者的帳戶，Phoenix 會替你執行智慧體。
- **外部應用程式**：用存取金鑰連接 MCP 用戶端，例如桌面助理或程式開發工具。應用程式自己的模型負責思考。
- **Home Assistant 自帶的助理**：Phoenix 可以作為語音智慧體回答、執行 AI 任務操作，或把自己的工具借給其他助理。

無論哪種方式，每個操作都會經過相同的檢查，並記錄在稽核日誌中。

相關：[{{label:agentchat.title}}](guide:window.agentChat)、[連線外部應用程式](guide:topic.connecting)、[語音智慧體、AI 任務或 Assist 工具提供方](guide:topic.homeAssistantFeatures)

## 每個操作要經過什麼 {#checks}

1. **能力**：金鑰整體上可以做哪類事情，例如控制裝置或編輯自動化。每項能力可設為拒絕存取、允許或確認。
2. **權限**：金鑰可以檢視和變更哪些分區、裝置和實體。
3. **MESA**：無論金鑰允許什麼，對每個裝置要有多謹慎：讓智慧體操作、先詢問你，還是不讓它碰。
4. **核准**：設為確認的操作會等待你核准。

相關：[智慧體可以做什麼](guide:topic.tools)、[安全](guide:topic.security)、[執行 Phoenix](guide:topic.running)、[管理 API](guide:topic.adminApi)、[{{label:accessKeys.cardCapabilities}}](guide:window.capabilities)、[權限如何判定](guide:topic.permissionResolution)、[MESA 如何決定](guide:topic.mesaRules)、[核准如何運作](guide:topic.approvalLifecycle)

## 為什麼不用長期存取權杖 {#token}

Home Assistant 長期存取權杖會以你的使用者身分在 Home Assistant 的所有介面中生效。任何持有它的人都能做你能做的事。

存取金鑰只對 Phoenix 有效。它有自己的權限、選用的有效期，以及只作用於 Phoenix 自身連線的速率限制。你可以撤銷它，而不必改動自己的登入。Phoenix 的連線不接受 Home Assistant 權杖。

## 從哪裡開始 {#start}

在{{label:shell.tabAccessKeys}}工作區中，選擇 **{{label:accessKeys.setUpAgent}}**。設定流程會帶你建立金鑰、設定其權限，並選擇 Agent Chat 或外部應用程式，最後讓你測試連線。

相關：[智慧體如何連線](guide:wizard.choose)、[安裝 Phoenix MCP](guide:topic.install)
