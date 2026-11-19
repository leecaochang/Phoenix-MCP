---
{ "id": "topic.commandRouting", "title": "命令如何路由", "parent": "window.settingsChat", "order": 3, "keywords": ["本機", "混合", "意圖", "句子比對", "捷徑", "快速路徑", "TypeSafe", "JEV"] }
---
像「關掉廚房的燈」這樣的簡單命令不需要完整的 AI 模型。開啟命令處理後，Phoenix 會先嘗試較便宜的方式。

## Phoenix 嘗試的順序 {#order}

1. Home Assistant 的句子比對，適用於所有已安裝的 Assist 語言。它在你的家中執行，不產生費用。
2. 決策提供者（如果你使用）。它處理句子比對遺漏的命令。
3. 你的 AI 模型，處理其他所有情況，與沒有命令處理時一樣。

一旦某一步執行了命令，後面的步驟就不會再使用。句子比對或決策提供者變更了某些內容時，回覆會說出執行的操作，讓你知道發生了什麼。

決策提供者執行命令前，Phoenix 還會問它你是否要求現在執行該操作。如果它判斷命令被否定、引用、延後或附帶條件（例如「不要打開床頭燈」），該命令會改為交給你的 AI 模型。

相關：[決策提供者](guide:window.settingsProviders#decision-provider)

## 何時適用 {#when}

分別為 [Agent Chat](guide:window.settingsChat#chat-commands) 和 [{{label:settings.voiceCard}}](guide:window.settingsHome#voice-commands) 開啟命令處理。

它只適用於純文字訊息。{{label:personas.home_companion.label}} 金鑰的訊息一律直接交給它處理。

## 後續訊息 {#follow-ups}

對話中後面的訊息也可以交給決策提供者。每則訊息會先對照對話進行檢查：它是否是真正的請求，以及之前的指示是否排除了它。如果這項檢查沒有把握，訊息會交給你的 AI 模型。

用「它」代替裝置名稱的後續訊息（例如「現在把它關掉」）適用於一盞燈：開啟或關閉它，或詢問它是否開著。

## 決策提供者從不執行的操作 {#risky-actions}

決策提供者根據你的話選擇操作。Phoenix 會檢查你是否說出了裝置名稱，但無法檢查操作本身。因此決策提供者從不執行以下操作：

- 解鎖或打開門鎖
- 打開或切換窗簾、車庫門或閥門
- 開啟或切換警報器
- 解除或觸發警報系統

這些命令會改為交給你的 AI 模型，就像決策提供者沒有把握時一樣。存取金鑰的權限和核准在那裡依然適用。

## 限制 {#limits}

決策提供者對每個請求有時間限制，並可設定每小時限制。達到任一限制時，命令會交給你的 AI 模型。

相關：[連線設定](guide:window.settingsProviders#decision-connection)

## 查看效果 {#effect}

路由統計會統計每則訊息的處理方式，讓你看到命令處理節省了什麼。

相關：[閱讀統計](guide:topic.routingStatistics)
