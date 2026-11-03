---
{ "id": "window.settingsChat", "title": "{{label:settings.agentcliCard}}", "parent": "workspace.settings", "order": 2, "keywords": ["聊天"], "sectionKeywords": { "chat-global": ["Shift+A", "快速鍵", "鍵盤"], "chat-memory": ["歷史", "記憶", "上下文"], "chat-steps": ["步驟", "工具呼叫"], "chat-style": ["語氣", "風格"] } }
---
Agent Chat（Phoenix 自己的聊天視窗）的設定。請在聊天視窗本身選擇存取金鑰、帳戶和模型。

## {{label:routing.enableChat}} {#chat-commands}

在不使用你的 AI 模型的情況下執行簡單命令，例如開燈。Phoenix 使用 Home Assistant 自己的句子比對，適用於所有已安裝的 Assist 語言。預設為關閉。

如果正在使用決策提供者，它會處理句子比對遺漏的命令。其他所有內容照常交給你的 AI 模型。

這不適用於 Home Companion 金鑰，也不適用於非純文字訊息。

相關：[決策提供者](guide:window.settingsProviders#decision-provider)、[命令如何路由](guide:topic.commandRouting)

## {{label:settings.agentcliGlobalLabel}} {#chat-global}

讓 Agent Chat 可以在任何 Home Assistant 頁面開啟，而不只是此面板。按 Shift+A 顯示或隱藏它。預設為開啟。

Shift+A 需要你的 Home Assistant 個人資料中開啟鍵盤快速鍵。在此面板中，即使該設定關閉，Shift+A 仍然有效。

變更會在頁面下次完整載入時生效。**{{label:settings.killSwitchCard}}** 會在所有地方關閉 Agent Chat。

## {{label:settings.agentcliScrollbackLabel}} {#chat-memory}

Phoenix 隨每則新訊息傳送的對話列數：0 到 5,000（預設 100）。

較早的輪次會整體捨棄，最新的輪次一律傳送。設為 0 時不傳送較早的輪次。更多歷史會給模型更多上下文，但每則訊息的費用也更高。

## {{label:settings.agentcliStepsLabel}} {#chat-steps}

模型針對一則訊息可以執行的最大工具步驟數：3 到 100（預設 20）。修正格式錯誤工具呼叫所用的步驟也計入。

達到限制時，Agent Chat 會詢問是否繼續。語音智慧體和 AI 任務使用相同的限制，但會停止。

## {{label:settings.conversationStyle}} {#chat-style}

回覆的語氣：{{label:settings.styleDirect}}（預設）、{{label:settings.styleWarm}}、{{label:settings.styleCalmGuide}}、{{label:settings.styleLively}} 或 {{label:settings.styleTechnical}}。這只改變措辭，不改變 Phoenix 的行為。

## {{label:settings.detailLevel}} {#chat-detail}

回覆說多少內容：{{label:settings.detailConcise}}（預設）、{{label:settings.detailBalanced}} 或 {{label:settings.detailDetailed}}。

## {{label:settings.homeFocused}} {#chat-home-focused}

要求模型只談論你的家，並拒絕其他話題。預設為關閉。

這是偏好，不是安全控制：模型是被要求，而不是被強制。它的存取金鑰決定它實際能做什麼。

相關：[存取金鑰](guide:workspace.accessKeys)

## {{label:routingStats.title}} {#chat-statistics}

**{{label:routingStats.show}}** 會開啟一個視窗，統計 Agent Chat 的訊息如何處理：由句子比對、決策提供者還是你的 AI 模型。

相關：[閱讀統計](guide:topic.routingStatistics)
