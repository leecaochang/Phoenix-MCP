---
{ "id": "window.permissionSummary", "title": "{{label:accessKeys.permissionSummary}}", "parent": "workspace.accessKeys", "order": 5, "keywords": ["概覽", "規則", "授權"] }
---
列出此金鑰[{{label:accessKeys.permissionsTree}}](guide:window.permissions)中設定的每條規則：每個設為 R、W 或 D 的域、裝置或實體。保留為 N 的列不會列出，它們繼承的內容也不會列出。

## 表格 {#summary-table}

顯示每條規則的類型、名稱、ID 和狀態。當裝置或域上的 D 覆蓋該規則時，狀態會顯示為拒絕存取。使用欄標題的選單來排序、移動或隱藏欄，拖曳標題邊緣可調整欄寬。

選擇一列可在{{label:accessKeys.permissionsTree}}中顯示它。實體列上的 MESA 按鈕會開啟其 MESA 策略。

## 空摘要 {#empty}

空摘要表示權限樹中沒有授予任何內容。不使用權限樹的工具，例如讀取設定或管理自動化，只要金鑰的能力允許，仍然可以運作。

在直通金鑰上，摘要會說明該金鑰略過權限樹。
