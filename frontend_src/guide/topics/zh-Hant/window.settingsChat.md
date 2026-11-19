---
{ "id": "window.settingsChat", "title": "{{label:settings.agentcliCard}}", "parent": "workspace.settings", "order": 2, "keywords": ["聊天"], "sectionKeywords": { "chat-global": ["Shift+A", "快速鍵", "鍵盤"], "chat-memory": ["歷史", "記憶", "上下文"], "chat-steps": ["步驟", "工具呼叫"], "chat-style": ["語氣", "風格"] } }
---
Agent Chat（Phoenix 自己的聊天視窗）的設定。請在聊天視窗本身選擇存取金鑰、帳戶和模型。

## {{label:routing.enableChat}} {#chat-commands}

在不使用你的 AI 模型的情況下執行簡單命令，例如開燈。Phoenix 使用 Home Assistant 自己的句子比對，適用於所有已安裝的 Assist 語言。預設為關閉。

如果正在使用決策提供者，它會處理句子比對遺漏的命令。其他所有內容照常交給你的 AI 模型。

這不適用於{{label:personas.home_companion.label}}金鑰，也不適用於非純文字訊息。

相關：[決策提供者](guide:window.settingsProviders#decision-provider)、[命令如何路由](guide:topic.commandRouting)

## {{label:settings.agentcliGlobalLabel}} {#chat-global}

讓 Agent Chat 可以在任何 Home Assistant 頁面開啟，而不只是此面板。按 Shift+A 顯示或隱藏它。預設為開啟。

Shift+A 需要你的 Home Assistant 個人資料中開啟鍵盤快速鍵。在此面板中，即使該設定關閉，Shift+A 仍然有效。

變更會在頁面下次完整載入時生效。**{{label:settings.killSwitchCard}}** 會在所有地方關閉 Agent Chat。

## {{label:settings.agentcliMemoryLabel}} {#chat-memory}

Phoenix 隨每則新訊息傳送的較早對話量，以字元計：0 到 960,000（預設 200,000）。你的新訊息也計入其中。

工具結果同樣計入，而且通常占比最大。較早的輪次會整體捨棄，從最早的開始。設為 0 時不傳送較早的輪次。

基本拉丁字母以外的字元（例如中文）每個按六個計算。

更多記憶會給模型更多上下文，但每則訊息的費用也更高。上下文視窗較小的模型（例如在本機執行的模型）需要更低的值。

Related: [長訊息](guide:window.agentChat#memory-warning)

## {{label:settings.agentcliStepsLabel}} {#chat-steps}

模型針對一則訊息可以執行的最大工具步驟數：3 到 100（預設 20）。修正格式錯誤工具呼叫所用的步驟也計入。

達到限制時，Agent Chat 會詢問是否繼續。語音智慧體和 AI 任務使用相同的限制，但會停止。

## {{label:settings.conversationStyle}} {#chat-style}

回覆的語氣。這只改變措辭，不改變 Phoenix 的行為。預設是 {{label:settings.styleDirect}}。

| 風格 | 效果 | 範例 |
|---|---|---|
| {{label:settings.styleDirect}} | 先給結果，沒有問候語或結語。 | 廚房燈已開啟。 |
| {{label:settings.styleWarm}} | 直接對您說話，回應您的請求，並以一句友善的話結尾。 | 都辦好了，我已經幫您開啟廚房燈。祝您晚上愉快！ |
| {{label:settings.styleCalmGuide}} | 使用簡單的詞語和編號步驟。解釋術語，並說明接下來會發生什麼。 | 廚房燈現在開著。如果想再關掉，直接告訴我就好。 |
| {{label:settings.styleLively}} | 輕快，用詞生動，在無關緊要時來一句俏皮話。 | 燈亮囉！廚房正式開張營業。 |
| {{label:settings.styleTechnical}} | 實體 ID、動作名稱、屬性和單位。 | light.kitchen: off to on via light.turn_on, brightness 255. |
| {{label:settings.styleExplanatory}} | 完成任務後，說明其中的原理，以及您如何自行操作。 | 廚房燈已開啟。運作方式：我對廚房區域的兩盞燈執行了 light.turn_on。 |

{{label:settings.styleEnglishOnly}} 下的風格會扮演一個角色，例如 {{label:settings.stylePirate}}、{{label:settings.styleNoirDetective}} 或 {{label:settings.styleStarshipComputer}}。無論詳細程度如何，角色都會在整則回覆中維持腔調。角色只適用於英文。以其他語言回覆時，會改用 {{label:settings.styleDirect}}。

遇到失敗、核准、安全問題和破壞性操作時，每種風格都不開玩笑，也不用角色腔調。數字、名稱以及某件事是否已發生，都保持準確。

選擇 {{label:settings.detailConcise}} 時，回覆最多三句短句，因此風格主要展現在用詞上。回覆與風格的貼合程度也取決於您的 AI 模型。

## {{label:settings.detailLevel}} {#chat-detail}

回覆說多少內容：{{label:settings.detailConcise}}（預設）、{{label:settings.detailBalanced}} 或 {{label:settings.detailDetailed}}。

## {{label:settings.homeFocused}} {#chat-home-focused}

要求模型只談論你的家，並拒絕其他話題。預設為關閉。

這是偏好，不是安全控制：模型是被要求，而不是被強制。它的存取金鑰決定它實際能做什麼。

相關：[{{label:shell.tabAccessKeys}}](guide:workspace.accessKeys)

## {{label:routingStats.title}} {#chat-statistics}

**{{label:routingStats.show}}** 會開啟一個視窗，統計 Agent Chat 的訊息如何處理：由句子比對、決策提供者還是你的 AI 模型。

相關：[閱讀統計](guide:topic.routingStatistics)
