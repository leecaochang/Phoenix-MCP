---
{ "id": "window.settingsHome", "title": "{{label:workspace.homeAssistant}}", "parent": "workspace.settings", "order": 3, "keywords": ["語音", "Assist", "助理"], "sectionKeywords": { "ai-decisions": ["評估", "ai_task.evaluate"], "voice-agent": ["對話智慧體", "管線"], "voice-assistant": ["管線"], "ai-task": ["產生資料"], "assist-tool-provider": ["LLM API", "控制 Home Assistant"] } }
---
把 Phoenix 連接到 Home Assistant 自己的 AI 功能。每個分頁設定一個功能，每個功能中都有一個存取金鑰決定 Phoenix 可以做什麼。

- {{label:settings.voiceCard}}：Phoenix 使用你的帳戶和模型回答 Home Assistant 的語音和 Assist 對話。
- {{label:settings.aiTaskCard}}：Phoenix 使用你的帳戶和模型執行 Home Assistant 的 AI 任務操作。
- {{label:settings.assistCard}}：另一個整合的模型使用 Phoenix 的工具。Phoenix 不執行自己的模型。

相關：[使用哪一個](guide:topic.homeAssistantFeatures)

## {{label:settings.voiceCard}} {#voice-agent}

讓 Phoenix 成為一個名為 Phoenix MCP 的 Home Assistant 對話智慧體。請先設定存取金鑰、帳戶和模型：三者都設定好後，智慧體才會出現。

### {{label:settings.voiceEnableLabel}} {#voice-enable}

開啟智慧體。預設為關閉。關閉它還會移除 Phoenix 建立的 Assist 助理。

**{{label:settings.killSwitchCard}}** 開啟時，智慧體仍會保留，但只會回覆它無法使用。

### {{label:settings.voiceAssistSetup}} {#voice-assistant}

建立一個名為 Phoenix MCP 的 Home Assistant 助理，使用此智慧體，並採用 Home Assistant 預設的語音轉文字和文字轉語音。智慧體完全設定好後可用。

關閉它會刪除助理。Home Assistant 始終保留至少一個助理，因此如果它是唯一的助理，它會保留，但 Phoenix 會停止管理它。

如果你清除存取金鑰、帳戶或模型，或撤銷金鑰，Phoenix 也會移除其助理。當它是唯一助理時，Home Assistant 會保留它，Phoenix 會告知你。新增另一個助理後，請在 Home Assistant 中移除它。

### {{label:settings.voicePreferredLabel}} {#voice-preferred}

讓 Phoenix 的助理成為 Home Assistant 預設使用的助理。關閉它會把這個角色交給 Home Assistant 清單中的第一個助理，它可能不是你之前使用的助理。

### {{label:settings.accessKey}} {#voice-access-key}

決定智慧體可以做什麼的存取金鑰。你只能選擇有效的金鑰。如果金鑰已撤銷或過期，它會在此處被清除。

Home Companion 金鑰會把對話交給 Home Companion 處理，並隱藏 **{{label:routing.enableVoice}}**。

### {{label:settings.providerAccount}} {#voice-provider}

其模型負責回答的帳戶。決策提供者帳戶不會列出。請先在[{{label:workspace.aiProviders}}](guide:window.settingsProviders)中新增帳戶。

### {{label:settings.model}} {#voice-model}

負責回答的模型。請先選擇帳戶。如果提供者停用該模型，Phoenix 會切換到其後續模型。

### {{label:routing.enableVoice}} {#voice-commands}

像在 Agent Chat 中一樣，在不使用你的 AI 模型的情況下執行簡單命令。預設為關閉。

相關：[命令如何路由](guide:topic.commandRouting)

### {{label:settings.conversationStyle}} {#voice-style}

回覆的語氣，選項與 [Agent Chat](guide:window.settingsChat#chat-style) 相同。預設：{{label:settings.styleDirect}}。

### {{label:settings.detailLevel}} {#voice-detail}

回覆說多少內容。預設：{{label:settings.detailConcise}}。

### {{label:settings.homeFocused}} {#voice-home-focused}

要求模型只談論你的家。這是偏好，不是安全控制。

### {{label:routingStats.title}} {#voice-statistics}

**{{label:routingStats.show}}** 會開啟一個視窗，統計語音命令的處理方式。

相關：[閱讀統計](guide:topic.routingStatistics)

## {{label:settings.aiTaskCard}} {#ai-task}

為 Home Assistant 提供一個名為 Phoenix MCP AI Task 的 AI 任務實體，自動化和腳本用它產生文字或資料。它在自己的存取金鑰範圍內代表整個家庭行動。如果 Home Assistant 告知任務由誰發起，任務只擁有該使用者的權限。

### {{label:settings.aiTaskEnableLabel}} {#ai-task-enable}

開啟實體。預設為關閉。只有當存取金鑰、帳戶和模型都已設定時，實體才會存在。

如果你清除了其中任何一項，Phoenix 會移除實體，你在 Home Assistant 中對它所做的任何變更都會遺失。

### {{label:settings.aiTaskDefaultLabel}} {#ai-task-default}

讓 Phoenix 的實體成為 Home Assistant 的預設 AI 任務。這會立即替換任何目前預設項目，無需詢問。快速說明會顯示它替換了哪一個。

關閉它會讓 Home Assistant 沒有預設項目。先前的預設項目不會恢復。

### {{label:settings.accessKey}} {#ai-task-access-key}

決定 AI 任務可以做什麼的存取金鑰。它的運作方式與[語音智慧體的](guide:#voice-access-key)相同。

### {{label:settings.providerAccount}} {#ai-task-provider}

其模型執行任務的帳戶。

### {{label:settings.model}} {#ai-task-model}

執行任務的模型。

### {{label:settings.conversationStyle}} {#ai-task-style}

文字結果的語氣。預設：{{label:settings.styleDirect}}。

### {{label:settings.detailLevel}} {#ai-task-detail}

文字結果說多少內容。預設：{{label:settings.detailBalanced}}。要求結構化資料的任務則使用它們自己的格式。

## {{label:settings.aiDecisionsHeading}} {#ai-decisions}

為 Home Assistant 新增第二個實體 Phoenix MCP Decisions，用於「評估」動作。自動化和腳本可以用它對傳入的文字或資料提出是非題、選擇題和評分題。決策提供者負責回答，每個回答都附帶機率。

此部分僅在 Home Assistant 2026.11 或更新版本上、且正在使用決策提供者時顯示。若要新增或選擇決策提供者，請前往 [{{label:workspace.aiProviders}}](guide:window.settingsProviders#decision-provider)。

問題和傳入的資料會傳送到你的決策提供者，每次傳送的請求都計入[請求限額](guide:window.settingsProviders#decision-request-limit)。依回答執行動作的自動化以 Home Assistant 的身分執行，不受 Phoenix 檢查。

發起「評估」動作的人需要對 Decisions 實體有控制權限，即使動作未指定實體。不是由人發起的自動化不需要。每次呼叫和拒絕都會寫入[稽核日誌](guide:topic.logRecords#audit-entries)。

### {{label:settings.aiDecisionsEnableLabel}} {#ai-decisions-enable}

開啟此實體。預設為關閉。只有在此開關開啟且已選擇存取金鑰時，實體才存在。

撤銷存取金鑰或移除決策提供者會關閉此功能並移除實體，你在 Home Assistant 中對它所做的變更也會遺失。

### {{label:settings.aiDecisionsDefaultLabel}} {#ai-decisions-default}

將其設為 Home Assistant 中未指定實體的「評估」動作的預設實體，並立即取代目前的預設實體。

### {{label:settings.accessKey}} {#ai-decisions-access-key}

允許「評估」動作執行的存取金鑰。它與 AI 任務的金鑰彼此獨立。它必須處於有效狀態，並將 **{{label:caps.cap_service_response.label}}** 設為 {{label:caps.modeAllow}}；直通金鑰已視為此設定。

實體權限和 MESA 在此不適用：此動作沒有目標實體，也不會變更任何內容。

## {{label:settings.assistCard}} {#assist-tool-provider}

把 Phoenix 的工具借給在別處執行的模型，例如設為使用 Phoenix MCP（限定範圍）控制 Home Assistant 的對話智慧體。Phoenix 在這裡不執行自己的模型。

### {{label:settings.assistBoundAccessKey}} {#assist-access-key}

另一個模型取得其工具的存取金鑰。使用 **{{label:settings.assistNotBound}}**（預設）時，不提供任何工具。**{{label:settings.killSwitchCard}}** 開啟時也是如此。

變更金鑰會拒絕先前輪次的工具呼叫，因此對話無法保留舊金鑰的權限。Home Assistant 的 MCP Server 使用同一個金鑰，且不受金鑰的速率限制。

相關：[透過 Home Assistant 的 MCP Server](guide:topic.connecting#ha-mcp-server)
