---
{ "id": "topic.commandRouting", "title": "命令如何路由", "parent": "window.settingsChat", "order": 3, "keywords": ["本機", "混合", "意圖", "句子比對", "捷徑", "快速路徑", "TypeSafe", "JEV"] }
---
像「關掉廚房的燈」這樣的簡單命令不需要完整的 AI 模型。開啟命令處理後，Phoenix 會先嘗試較便宜的方式。

## Phoenix 嘗試的順序 {#order}

1. Home Assistant 的句子比對，適用於所有已安裝的 Assist 語言。它在你的家中執行，不產生費用。
2. 決策提供者（如果你使用）。它處理句子比對遺漏的命令。
3. 你的 AI 模型，處理其他所有情況，與沒有命令處理時一樣。

一旦某一步執行了命令，後面的步驟就不會再使用。

相關：[決策提供者](guide:window.settingsProviders#decision-provider)

## 何時適用 {#when}

你可以分別為 [Agent Chat](guide:window.settingsChat#chat-commands) 和[語音智慧體](guide:window.settingsHome#voice-commands)開啟命令處理。

它只適用於純文字訊息。Home Companion 金鑰的訊息一律直接交給 Home Companion。

## 後續訊息 {#follow-ups}

對話中的第一則訊息可以交給決策提供者。後續訊息只有在使用 TypeSafe / JEV 模型，且語言受該模型支援時才會交給它。否則會交給你的 AI 模型。

## 限制 {#limits}

決策提供者對每個請求有時間限制，並可設定每小時限制。達到任一限制時，命令會交給你的 AI 模型，確保不會無人回應。

相關：[連線設定](guide:window.settingsProviders#decision-connection)

## 查看效果 {#effect}

路由統計會統計每則訊息的處理方式，讓你看到命令處理節省了什麼。

相關：[閱讀統計](guide:topic.routingStatistics)
