---
{ "id": "window.review", "title": "{{label:workspace.review}}", "parent": "workspace.approvals", "order": 2, "keywords": ["差異", "比較", "決定"], "sectionKeywords": { "reject": ["原因", "拒絕請求"], "change": ["更改前", "更改後", "預覽", "程式碼編輯器", "並排"] } }
---
準確顯示所選請求會變更什麼，並讓你做出決定。已決定的請求會保留在這裡，並顯示其結果。

## 剩餘時間 {#context}

標題下方的列顯示請求的狀態、它的存取金鑰，以及待處理請求過期前的剩餘時間。已過期的請求無法核准，因此只提供 **{{label:approvals.reject}}**，它會把請求以已過期狀態關閉。

## {{label:approvals.approve}} {#approve}

立即執行請求，不再詢問。Phoenix 會先檢查它是否仍被允許：金鑰必須仍然有效，其能力不能是拒絕存取，緊急停止開關必須關閉。否則請求會在未執行的情況下關閉。

如果操作失敗，請求會以失敗結束，並且不會再提供。如果你仍然想要該變更，請讓智慧體重試。

相關：[你核准後會發生什麼](guide:topic.approvalLifecycle#approve)

## {{label:approvals.reject}} {#reject}

拒絕請求。**{{label:approvals.reject}}** 會開啟一個用於填寫選填原因的方塊，智慧體會收到該原因，以便調整下一次提議。按 Enter 或選擇 **{{label:approvals.confirmReject}}** 拒絕，或選擇 **{{label:approvals.cancelReject}}** 退出。

你輸入的原因也會同步到 Agent Chat 的拒絕按鈕。

## 變更 {#change}

變更的顯示方式取決於請求做什麼：

- 設定和 YAML：**{{label:common.before}}** 和 **{{label:common.after}}** 並排顯示。在 **{{label:changes.lineViewShort}}** 和 **{{label:changes.codeEditorShort}}** 之間切換，或把兩側上下堆疊。
- 儀表板、卡片和能源儀表板：**{{label:common.diff}}** 或 **{{label:common.preview}}**，後者會繪製結果。
- 對裝置的操作：操作、其目標，以及 MESA 允許、暫緩或阻止的內容。
- 刪除待辦事項：每個事項及其狀態，並提示刪除後無法復原。

這些檢視選擇會儲存在此瀏覽器中，變更也會使用它們。

## 在請求間切換 {#step}

**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 可在不離開審核的情況下在清單中移動。
