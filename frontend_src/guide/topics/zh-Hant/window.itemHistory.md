---
{ "id": "window.itemHistory", "title": "{{label:workspace.itemHistory}}", "parent": "workspace.changes", "order": 3, "keywords": ["版本", "時間軸", "較早"] }
---
所選項目的每筆已記錄變更，最新的在前，並標記所選的那一筆。選擇一個變更進行比較，**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 隨後會在此清單中移動。

當項目有不止一個變更時，它會自行在變更清單下方開啟。如果你關閉了它，它會保持關閉；可從 **{{label:workspace.options}}** 重新開啟。

Phoenix 會保留每個項目最新的 20 次變更。實體重新命名後，其變更會移到新的 ID，因此歷史仍保留在一個清單中。
