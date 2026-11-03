---
{ "id": "window.auditLog", "title": "{{label:shell.tabAudit}}", "parent": "workspace.logs", "order": 1, "keywords": ["稽核", "請求", "歷史"], "sectionKeywords": { "list": ["欄", "排序", "載入更多", "MESA 建議", "來源"], "filters": ["結果", "存取金鑰", "時間", "方法", "資源", "請求 ID", "搜尋"], "updates": ["暫停", "重新整理", "即時"], "attention": ["需要關注", "在稽核日誌中顯示"] } }
---
智慧體和管理員透過 Phoenix 採取的每個操作，最新的在前。

相關：[日誌記錄的內容](guide:topic.logRecords)

## 清單 {#list}

選擇一筆條目，即可在[{{label:workspace.logDetails}}](guide:window.logDetails)中閱讀它。上下方向鍵可在清單中移動。

- **{{label:audit.rowAccess}}** 說明金鑰是使用了其權限還是[直通模式](guide:topic.passThrough)。
- **{{label:audit.rowMesa}}** 在 MESA 警告智慧體但允許操作執行時顯示 {{label:mesa.modeAdvisory}}。
- **{{label:audit.rowIp}}** 是呼叫方的 IP 位址，或發起呼叫的 Phoenix 功能，例如 Agent Chat。
- **{{label:audit.colAccessKey}}** 顯示金鑰的目前名稱，即使事後已重新命名。

日誌一次載入 100 筆條目；選擇 **{{label:common.loadMore}}** 載入更多。依最新優先之外的任何順序排序，只會對已載入的條目排序。

從欄標題的選單排序、移動和隱藏欄，拖曳標題邊緣可調整欄寬。

## {{label:logs.filters}} {#filters}

依 **{{label:audit.rowOutcome}}**、**{{label:audit.colAccessKey}}** 和時間，以及 **{{label:audit.rowMethod}}**、**{{label:audit.rowResource}}** 和 **{{label:audit.rowIp}}** 中的文字縮小清單範圍。**{{label:audit.rowRequestId}}** 可尋找一個確切的請求，例如來自對話記錄的請求。像過去 5 分鐘這樣的時間範圍會隨日誌更新而移動。

隱藏篩選會停止套用它們，直到你再次顯示。兩個日誌都開啟時，一個 **{{label:logs.filters}}** 按鈕會顯示兩者的篩選條件。

## 即時更新 {#updates}

視窗顯示且未摺疊時，新條目每 5 秒出現一次。**{{label:logs.pauseUpdates}}** 會在你閱讀時讓清單保持不動。暫停期間，**{{label:common.refresh}}** 會載入一次最新條目。

## 來自需要關注 {#attention}

需要關注視窗中的 **{{label:attention.showInAudit}}** 會開啟此日誌，並篩選到那一個請求。
