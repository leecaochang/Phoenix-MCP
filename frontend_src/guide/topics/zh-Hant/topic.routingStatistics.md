---
{ "id": "topic.routingStatistics", "title": "閱讀路由統計", "parent": "window.settingsChat", "order": 2, "keywords": ["計數", "百分比", "token", "用量", "重設"] }
---
路由統計顯示 Phoenix 有多少次在沒有使用你的 AI 模型的情況下執行了命令，以及其餘部分花費了多少。Agent Chat、語音智慧體和「評估」動作分別計數。

## 總體數字 {#headline}

頂端的百分比是由句子比對或決策提供者處理的訊息占比。它越高，你支付的完整模型輪次就越少。

## 計數 {#counts}

每則訊息依其結果計數一次。如果某則訊息在任何時候使用了你的 AI 模型，它就算作傳送給該模型。

- **{{label:routingStats.local}}**：由 Home Assistant 的句子比對完成。
- **{{label:routingStats.decision}}**：由決策提供者完成。
- **{{label:routingStats.provider}}**：由你的 AI 模型處理。
- **{{label:routingStats.pending}}**：等待你的核准。
- **{{label:routingStats.failed}}** 和 **{{label:routingStats.cancelled}}**：未完成的訊息。

傳送給你的 AI 模型的訊息也可能失敗或被取消，因此各列相加不一定等於總數。

## 評估 {#evaluate}

「評估」視窗統計 [Decisions 實體](guide:window.settingsHome#ai-decisions)處理的「評估」動作。頂部數字是呼叫次數，因為每次呼叫都會傳送給決策提供者。資料產生不計入。

- **{{label:routingStats.evaluated}}**：決策提供者給出了回答。
- **{{label:routingStats.refused}}**：實體拒絕了這次呼叫，例如發起者對它沒有控制權限，或已達到請求上限。
- **{{label:routingStats.failed}}** 和 **{{label:routingStats.cancelled}}**：未完成的呼叫。

它只顯示決策 token。

## Token {#tokens}

Token 列彙總決策提供者和你的 AI 模型回報的用量。提供者不一定會回報用量。沒有回報的請求會計入總數下方。

## 重新整理和重設 {#refresh-and-reset}

**{{label:common.refresh}}** 會載入最新計數。**{{label:routingStats.reset}}** 會在你確認後把此視窗的計數歸零。重設後，仍在處理中的訊息不會被計入。

計數每 30 秒儲存一次，重新啟動後仍會保留。在[資料管理](guide:window.settingsSafety#wipe-core)中刪除核心資料會清除它們。
