---
{ "id": "window.mesaProfiles", "title": "{{label:workspace.mesaProfiles}}", "parent": "workspace.mesa", "order": 1, "keywords": ["MESA", "策略清單"], "sectionKeywords": { "add": ["新策略", "建立"], "filters": ["搜尋", "類型", "控制模式", "執行模式", "繼承"], "list": ["欄", "供應商", "來源", "標籤"], "delete": ["移除"], "actions": ["重新掃描", "恢復已忽略"], "import": ["存檔", "備份", "替換", "上傳"], "export": ["存檔", "備份", "下載"] } }
---
每條 MESA 策略，無論涵蓋什麼，都在一個清單中。清單顯示期間每 30 秒更新一次。

## {{label:mesa.addProfile}} {#add}

在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中開始建立新策略。選擇它涵蓋的內容：實體、裝置、分區、整合或域。在你於那裡選擇 **{{label:mesa.save}}** 之前，不會儲存任何內容。

相關：[層級](guide:topic.mesaRules#levels)

## {{label:logs.filters}} {#filters}

依 **{{label:mesa.colAppliesTo}}**、**{{label:mesa.colControl}}** 和 **{{label:mesa.fieldEnforcement}}** 縮小清單範圍。每個選項會顯示符合多少條策略。**{{label:mesa.modeInherited}}** 表示該策略未設定控制模式。

搜尋會符合 ID、名稱、類型和標籤，並在篩選隱藏時繼續有效。隱藏篩選會停止套用它們，直到你再次顯示。變更篩選會關閉已開啟的策略。

## 清單 {#list}

選擇一條策略，即可在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中開啟它。策略被強制執行時，控制模式旁會顯示「{{label:mesa.modeEnforced}}」。

**{{label:mesa.colTags}}** 和 **{{label:mesa.colSource}}** 最初為隱藏；可從欄選單中顯示。**{{label:mesa.colSource}}** 對整合隨程式碼提供的策略顯示 **{{label:mesa.vendorBadge}}**。

## {{label:mesa.delete}} {#delete}

移除策略。選擇 **{{label:mesa.delete}}**，然後選擇 **{{label:common.confirm}}**，或點選其他任意位置退出。它涵蓋的內容隨後遵循其他策略，或者如果沒有剩餘策略，則遵循內建基線。

整合自己的供應商策略會在 Home Assistant 下次啟動時恢復，除非你已為該整合儲存了自己的策略。

## MESA 操作 {#actions}

選單包含：

- **{{label:mesa.importTitle}}** 和 **{{label:mesa.exportTitle}}**，見下文。
- **{{label:mesa.suggestTitle}}**，用於顯示或隱藏該視窗。
- **{{label:mesa.rescan}}**，會立即尋找新的[建議](guide:window.mesaSuggestions)和建議性提示中的發現。
- **{{label:mesa.restoreDismissed}}**，會恢復你忽略的所有建議。
- **{{label:mesa.restoreNotices}}**，會恢復你在[{{label:workspace.advisory}}](guide:window.mesaAdvisory)中忽略的發現。

## {{label:mesa.importTitle}} {#import}

從 MESA 存檔檔案（例如你匯出的檔案）中新增策略。選擇 **{{label:mesa.browse}}** 並選擇檔案。Phoenix 會在匯入前顯示存檔包含的內容。

這裡已存在的策略會保持原樣。開啟 **{{label:mesa.replaceExisting}}** 可改為覆蓋它們；按鈕會變紅並顯示 **{{label:mesa.importAndReplace}}**。不會再要求確認。

匯入的策略會立即生效。Phoenix 會檢查每條策略，並列出它拒絕的策略。針對此家庭沒有的目標的策略會出現在[{{label:workspace.advisory}}](guide:window.mesaAdvisory#orphans)中。

存檔可以包含策略編輯器無法設定的欄位，例如宣告限制、時間範圍和部署預設設定。

相關：[策略編輯器無法設定什麼](guide:topic.mesaRules#not-in-editor)

## {{label:mesa.exportTitle}} {#export}

把所有策略下載為一個存檔檔案，如有部署預設設定也會包含。選擇 **{{label:mesa.export}}** 以確認。該檔案包含你完整的智慧體安全策略，請妥善保管。
