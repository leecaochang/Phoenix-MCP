---
{ "id": "window.approvals", "title": "{{label:shell.tabApprovals}}", "parent": "workspace.approvals", "order": 1, "keywords": ["請求", "佇列", "待處理"], "sectionKeywords": { "select-multiple": ["批次", "全部核准", "批次處理"], "show": ["歷史", "篩選", "已核准", "已拒絕", "已過期"] } }
---
智慧體發出的請求。選擇一個請求，即可在[{{label:workspace.review}}](guide:window.review)中查看它。清單會自行更新，無需重新整理。

## {{label:approvals.show}} {#show}

要列出哪些請求：**{{label:approvals.tabPending}}**（預設）、**{{label:approvals.tabHistory}}**（所有已決定的請求）、**{{label:approvals.scopeAll}}**，或單一結果。你的選擇會儲存在此瀏覽器中。

如果你在查看歷史時有新請求到達，再次開啟工作區會切回待處理。

## 搜尋 {#search}

在已載入的請求中，依存取金鑰、工具、標題或原因尋找請求。

## 清單 {#list}

每一列顯示請求及其狀態。更多欄，例如存取金鑰、工具和時間，預設為隱藏；可從欄標題的選單中顯示。最新的請求在最前。

使用方向鍵在清單中移動；審核會跟隨。正在被某人核准的請求會顯示為 **{{label:approvals.processing}}**。

## {{label:approvals.selectMultiple}} {#select-multiple}

一次核准多個待處理請求。在工具列上選擇 **{{label:approvals.selectMultiple}}**，勾選請求，然後選擇顯示已選數量的核准按鈕。列出至少兩個待處理請求時可用。

Phoenix 會從最新開始逐個核准，並在第一個未成功的請求處停止。其餘請求保持待處理並保持勾選。一批最多包含 50 個請求，因此全選只會勾選前 50 個。已過期的請求無法勾選。**{{label:approvals.cancelSelection}}** 會退出而不核准。

僅對你已經非常了解的請求使用它；每個請求仍會完全依照審核顯示的內容執行。

## {{label:common.loadMore}} {#load-more}

載入接下來的 50 個請求。
