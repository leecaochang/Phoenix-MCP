---
{ "id": "window.mesaSuggestions", "title": "{{label:mesa.suggestTitle}}", "parent": "workspace.mesa", "order": 3, "keywords": ["MESA", "建議", "未受保護", "影響範圍"], "sectionKeywords": { "why": ["門鎖", "警報", "車庫", "閥門", "警報器", "熱水器", "韌體", "自動化", "腳本", "場景"], "review": ["套用", "接受"], "dismiss": ["隱藏", "恢復", "重新掃描"] } }
---
可能需要 MESA 策略的裝置和自動化。Phoenix 絕不會自行套用建議。指向某一列，可在快速說明中閱讀建議的原因。

## 為什麼建議 {#why}

**{{label:mesa.signalUnprotected}}**：沒有任何層級策略涵蓋的風險裝置。門鎖和警報控制面板會被建議為 {{label:mesa.modeProhibited}}。車庫門、門和閘門、閥門、警報器、熱水器和韌體更新會被建議為 {{label:mesa.modeConfirm}}。

當同一類型有 5 個或更多未被涵蓋時，會合併為一條域建議，車庫門、門和閘門除外。

**{{label:mesa.signalBlastRadius}}**：沒有策略、且使用風險裝置或 8 個或更多實體的自動化、腳本或場景。它會被建議為 {{label:mesa.modeConfirm}}，這樣智慧體在啟動它之前會先詢問。

以模板表示的目標無法被看到，因此永遠不會被建議。

## 處理建議 {#review}

選擇一列，在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中作為新策略開啟，並填入建議的模式。檢查它，視需要修改，然後選擇 **{{label:mesa.save}}**。儲存後建議會消失。

## {{label:mesa.dismiss}} {#dismiss}

對使用此家庭的所有人隱藏你不想要的建議。選擇 **{{label:mesa.dismiss}}**，然後選擇 **{{label:common.confirm}}**。

若要再次看到已忽略的建議，請在 MESA 操作選單中選擇 **{{label:mesa.restoreDismissed}}**。同一選單中的 **{{label:mesa.rescan}}** 會立即尋找新建議；否則清單每 30 秒更新一次。
