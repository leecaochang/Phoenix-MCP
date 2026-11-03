---
{ "id": "window.mesaEditor", "title": "{{label:workspace.profileEditor}}", "parent": "workspace.mesa", "order": 2, "keywords": ["MESA", "編輯策略"], "sectionKeywords": { "target": ["實體", "裝置", "分區", "整合", "域"], "tags": ["語意標籤", "能力標籤"], "control-mode": ["自主", "確認", "唯讀", "禁止", "未設定", "核准"], "enforcement": ["建議", "強制執行"], "triggers-automations": ["可能", "觸發校驗"], "effect": ["可復原", "復原代價", "副作用範圍"], "privacy-level": ["公開", "一般", "敏感", "受限"], "save": ["放棄", "未儲存"], "effective-resolution": ["繼承", "來源", "來自", "解析告警"], "from-home-assistant": ["對話方塊", "內容相關", "加號按鈕"] } }
---
你在[{{label:workspace.mesaProfiles}}](guide:window.mesaProfiles)中選擇的策略，或一個新策略。標題說明它是什麼類型的策略以及涵蓋什麼。

## 涵蓋範圍 {#target}

對於新策略，從清單中選擇它涵蓋的內容。已經有此類策略的目標不會提供。策略儲存後，其目標無法變更。

## {{label:mesa.semanticTags}} {#tags}

標準的 MESA 標籤，告訴智慧體裝置是什麼、能做什麼，例如門鎖或加熱器。標籤不會改變智慧體可以做什麼。最多新增 6 個。**{{label:mesa.showSuggestions}}** 會列出適合該域的標籤。

## {{label:mesa.fieldControlMode}} {#control-mode}

智慧體可以如何變更策略涵蓋的內容：

- **{{label:mesa.modeAutonomous}}**：智慧體可以自由操作。
- **{{label:mesa.modeConfirm}}**：MESA 強制執行時，操作會等待你的核准。
- **{{label:mesa.modeReadOnly}}**：除關閉外的任何 MESA 模式下，智慧體都不能變更它。
- **{{label:mesa.modeProhibited}}**：MESA 強制執行時，智慧體不能對它操作。在 {{label:settings.mesaModeAdvisory}} 模式下，它只會警告智慧體。
- **{{label:mesa.unset}}**：策略不做決定。由其他策略或內建基線決定，清單會顯示 **{{label:mesa.modeInherited}}**。

新建的實體策略會從實體目前的模式開始。其他新策略會從 {{label:mesa.unset}} 開始。

設定模式會替換內建基線，即使模式更寬鬆。確認的門鎖域策略會讓每把門鎖變為確認，而不是禁止。

相關：[策略如何組合](guide:topic.mesaRules#combining)、[內建基線](guide:topic.mesaRules#baseline)

## {{label:mesa.fieldEnforcement}} {#enforcement}

當設定中的 **{{label:settings.mesaModeLabel}}** 為 {{label:settings.mesaModeAdvisory}} 時，**{{label:mesa.modeEnforced}}** 會強制執行此策略。可用於只對一個裝置嚴格，而不讓 MESA 在所有地方都嚴格。設定為關閉時，不會強制執行任何內容。

**{{label:mesa.modeAdvisory}}**（預設）交由設定決定。它不會被儲存，因此更廣泛且設為強制的策略仍然適用。

相關：[操作違反規則時會發生什麼](guide:topic.mesaRules#modes)

## {{label:mesa.fieldTriggersAutomations}} {#triggers-automations}

變更此項是否可能啟動自動化。MESA 會用它對自動化進行檢查：標記為 **{{label:mesa.trigNone}}** 但仍被自動化使用時，會在[{{label:workspace.advisory}}](guide:window.mesaAdvisory#validation)中顯示一筆發現。預設：**{{label:mesa.trigUnknown}}**。

## 描述效果 {#effect}

**{{label:mesa.fieldReversible}}**、**{{label:mesa.fieldReversibilityCost}}** 和 **{{label:mesa.fieldSideEffectScope}}** 告訴智慧體變更是否可以復原、復原的代價，以及效果影響的範圍。它們為智慧體提供資訊，但不會阻止任何操作。每項預設都是 {{label:mesa.unset}}。

## {{label:mesa.fieldPrivacyLevel}} {#privacy-level}

此策略向智慧體公開的資料有多敏感。預設：**{{label:mesa.privNormal}}**。**{{label:mesa.privRestricted}}** 會讓自主策略需要確認。

相關：[隱私](guide:topic.mesaRules#privacy)

## 儲存 {#save}

**{{label:mesa.save}}** 會儲存策略。它會立即生效。對於已有策略，在你變更內容後儲存才可用。

**{{label:mesa.discardChanges}}** 會把表單恢復為已儲存的狀態，或關閉新策略。在清單中選擇另一條策略也會替換表單，因此未儲存的變更會遺失。

儲存會用表單顯示的內容替換整個策略。表單不顯示的欄位，例如匯入的宣告限制或時間範圍，會被移除。供應商策略會變為你自己的策略。

相關：[策略編輯器無法設定什麼](guide:topic.mesaRules#not-in-editor)

### 觸發警告 {#save-warnings}

儲存實體策略後，Phoenix 會用它對自動化進行檢查。如果發現問題，策略仍會儲存，警告會列在表單下方。選擇 **{{label:mesa.dismiss}}** 可關閉它們。

### {{label:mesa.delete}} {#delete}

在你就地確認後移除策略。它涵蓋的內容隨後遵循其他策略，或內建基線。

## {{label:mesa.effectiveResolution}} {#effective-resolution}

對於已儲存的實體策略，這是合併所有層級後 MESA 實際使用的值。**{{label:mesa.colFrom}}** 註明每個值來自哪個層級。**{{label:mesa.colOrigin}}** 說明由誰寫入：user 表示你，developer 表示整合供應商策略。

其上的 **{{label:mesa.resolutionWarnings}}** 表示有規則未能如原樣生效，例如 MESA 忽略了某個覆蓋。警告來自 MESA 本身，且為英文。

## 來自 Home Assistant 的頁面 {#from-home-assistant}

開啟策略按鈕後，同一個編輯器會從 Home Assistant 自己的頁面以對話方塊形式開啟，針對你正在檢視的實體、裝置、分區或整合。取消時會先詢問，再捨棄你的變更。

相關：[{{label:settings.injectLabel}}](guide:window.settingsGeneral#inject)
