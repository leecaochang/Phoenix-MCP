---
{ "id": "window.changes", "title": "{{label:shell.tabChanges}}", "parent": "workspace.changes", "order": 1, "keywords": ["歷史", "清單", "版本"], "sectionKeywords": { "filters": ["類型", "操作", "搜尋"] } }
---
Phoenix 記錄的每次變更，最新的在前。選擇一個變更，即可在[{{label:workspace.comparison}}](guide:window.comparison)中比較它。智慧體進行變更時，清單會自行更新。

## {{label:logs.filters}} {#filters}

工具列上的 **{{label:logs.filters}}** 會顯示篩選條件：類型、操作（建立、編輯、刪除或回滾）以及搜尋方塊。搜尋會在已載入的變更中，依項目、類型、操作、發起者和摘要尋找。

變更篩選條件或搜尋內容會清除所選的變更。關閉篩選會停止套用它們；再次開啟時會恢復原狀。**{{label:logs.clearFilters}}** 會重設這三項。

## 清單 {#list}

每一列顯示變更的時間、項目、操作、類型以及發起者：存取金鑰，或 **{{label:changes.byAdmin}}** 表示從此面板進行的還原。

從欄標題的選單排序、移動和隱藏欄，拖曳標題邊緣可調整欄寬。清單下方的計數包含尚未載入的變更。

## {{label:common.loadMore}} {#load-more}

載入接下來的 100 筆變更。
