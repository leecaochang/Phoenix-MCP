---
{ "id": "window.mesaAdvisory", "title": "{{label:workspace.advisory}}", "parent": "workspace.mesa", "order": 4, "keywords": ["MESA", "發現", "提示", "警告"], "sectionKeywords": { "inspection": ["模板", "藍圖", "部分檢查"], "validation": ["觸發", "條件", "觸發自動化"], "orphans": ["孤立", "缺少", "已刪除目標"], "dismiss": ["隱藏", "恢復提示"], "refresh": ["過期", "更新", "過時"] } }
---
MESA 在檢查你的策略和自動化時注意到的事情。視窗僅在有事可顯示時出現。這些內容都不會改變 MESA 對待操作的方式。

## 部分檢查的自動化 {#inspection}

使用模板或藍圖的自動化，因此 MESA 無法讀取它們觸及的每個實體。開啟 **{{label:mesa.viewAutomationDetails}}** 可列出它們。你不需要做任何事。

## 觸發發現 {#validation}

某實體的策略說 **{{label:mesa.fieldTriggersAutomations}}** 為 {{label:mesa.trigNone}}，但有自動化使用了它。在觸發器中這是錯誤，在條件中是警告。每筆發現都會說明要變更什麼，在策略中還是在自動化中。

相關：[{{label:mesa.fieldTriggersAutomations}}](guide:window.mesaEditor#triggers-automations)

## 孤立的策略 {#orphans}

其實體、裝置、分區或整合已不存在的策略，例如在你移除裝置之後。整合在沒有任何實體且未載入時算作已消失。選擇一條策略，即可在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中開啟並刪除它。Phoenix 絕不會替你刪除它們。

## {{label:mesa.dismiss}} {#dismiss}

在此瀏覽器中隱藏一組發現。當新發現加入該組時，它會再次出現。在 MESA 操作選單中選擇 **{{label:mesa.restoreNotices}}** 可再次看到已忽略的發現。

## 發現何時更新 {#refresh}

MESA 會在 Home Assistant 啟動、自動化重新載入，以及你在此儲存、刪除或匯入策略時再次檢查，因此已修復的發現會消失。**{{label:mesa.rescan}}** 會立即再次檢查。
